"""Discovery selects candidates; only fresh complete formal blocks prove capacity."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'lib'))
from adaptive_search import run, recoverable_load_limit
from protocol import parse_args, capacities, comparisons, baseline_readiness


LOAD_LIMIT = 'k6 exhausted its fixed VU pool (Insufficient VUs); arrivals were dropped'


class AdaptiveSearchTest(unittest.TestCase):
    def search(self, thresholds, *, plan='kafka-buffering', rule=None, budget=float('inf'), steps=8, refine=4):
        args = parse_args([plan, '--probe-steps', str(steps), '--refine-steps', str(refine)])
        calls, results, meta = [], [], {}
        elapsed = [0]

        def execute(point, phase):
            calls.append(dict(point))
            elapsed[0] += point['seconds']+args.request_timeout+10
            outcome = rule(point, phase) if rule else ('pass' if point['rate'] <= thresholds[point['path']] else 'fail')
            value = {**point, 'valid': outcome != 'limit', 'sloPassed': outcome == 'pass',
                     'loadErrors': [LOAD_LIMIT] if outcome == 'limit' else [],
                     'correctnessErrors': [], 'unresolvedCount': 0,
                     'classes': {'eligible': {'responseMs': {'samples': point['scheduled']//(10 if plan == 'redis-filtering' else 1),
                                                           'p99': 100 if outcome == 'pass' else 800}}},
                     'completionWindowRps': point['rate']}
            if phase == 'confirmation': results.append((str(len(calls)), value))
            return value

        run(args, execute, results, meta, lambda: budget-elapsed[0], lambda: None)
        return args, calls, results, meta, capacities(args, results)

    def test_mixed_search_skips_low_ladder_and_confirms_only_near_fixed_start_boundary(self):
        args, calls, rows, meta, bounds = self.search({'A': 700, 'B': 850}, plan='redis-filtering')
        self.assertEqual([500, 500], [p['rate'] for p in calls[:2]])
        self.assertTrue(all(p['seconds'] == 20 and p['repeat'] == 1 for p in calls if p['phase'] == 'probe'))
        self.assertTrue(all(p['seconds'] >= 60 and p['rate'] >= 500 for p in calls if p['phase'] == 'confirmation'))
        self.assertLessEqual(len(rows), 15)  # Two bounds per path, plus at most one shared comparison block.
        self.assertEqual({1, 2, 3}, {p['repeat'] for _, p in rows})
        self.assertTrue(all(b['bounded'] and not baseline_readiness(b) for b in bounds.values()))
        self.assertEqual('bounds-resolved', meta['search']['stopReason'])
        self.assertTrue(any(c['latencyReductionPct'] is not None for c in comparisons(args, rows)))

    def test_short_probes_cannot_become_capacity_or_paired_comparison_evidence(self):
        args, calls, _, meta, _ = self.search({'B': 330, 'C': 450})
        probes = [('probe', {**p, 'valid': True, 'sloPassed': True,
                            'classes': {'eligible': {'responseMs': {'samples': 100000, 'p99': 1}}},
                            'completionWindowRps': p['rate']}) for p in calls if p['phase'] == 'probe']
        self.assertTrue(all(b['passingRps'] is None for b in capacities(args, probes).values()))
        self.assertEqual([], comparisons(args, probes))
        self.assertTrue(all(not p['qualifiedForCapacity'] and p['scope'] == 'validation'
                            for state in meta['search']['paths'].values() for p in state['probes']))

    def test_shared_latency_anchor_is_confirmed_in_adjacent_paired_rounds(self):
        _, calls, rows, meta, _ = self.search({'B': 330, 'C': 450})
        first = [p for p in calls if p['phase'] == 'confirmation'][:6]
        self.assertEqual(['B', 'C', 'C', 'B', 'B', 'C'], [p['path'] for p in first])
        self.assertEqual(1, len({p['rate'] for p in first}))
        self.assertEqual(first[0]['rate'], meta['search']['comparisonAnchorRps'])

    def test_slower_machine_descends_from_fixed_start_and_formally_remeasures_lower_candidates(self):
        _, calls, rows, _, bounds = self.search({'B': 180, 'C': 220})
        self.assertEqual([300, 300], [p['rate'] for p in calls[:2]])
        self.assertTrue(any(p['rate'] < 200 for p in calls))
        self.assertTrue(all(b['bounded'] for b in bounds.values()))
        self.assertLessEqual(bounds['B']['passingRps'], 180)
        self.assertTrue(all(p['seconds'] >= 60 for _, p in rows))

    def test_all_pass_ends_with_fresh_formal_lower_bound_instead_of_full_ladder(self):
        _, calls, rows, meta, bounds = self.search({'B': 10000, 'C': 10000}, steps=3)
        self.assertEqual(6, len(rows))
        self.assertTrue(all(b['passingRps'] == 469 and b['failingRps'] is None for b in bounds.values()))
        self.assertEqual('inconclusive', meta['search']['stopReason'])
        self.assertEqual(6, sum(p['phase'] == 'probe' for p in calls))

    def test_generator_limited_probe_is_not_an_upper_bound_and_other_path_continues(self):
        def outcome(p, phase):
            if p['path'] == 'B': return 'limit' if p['rate'] >= 350 else 'pass'
            return 'pass' if p['rate'] <= 450 else 'fail'
        _, calls, _, meta, bounds = self.search({}, rule=outcome)
        self.assertIsNotNone(bounds['B']['passingRps'])
        self.assertIsNone(bounds['B']['failingRps'])
        self.assertTrue(bounds['C']['bounded'])
        self.assertEqual('generator-limited', meta['search']['paths']['B']['stopReason'])
        self.assertFalse(any(p['path'] == 'B' and p['rate'] >= 350 and p['phase'] == 'confirmation' for p in calls))

    def test_long_confirmation_can_saturate_then_fall_back_without_repeating_invalid_block(self):
        def outcome(p, phase):
            if p['path'] == 'B' and phase == 'confirmation' and p['rate'] >= 330: return 'limit'
            return 'pass' if p['rate'] <= (350 if p['path'] == 'B' else 450) else 'fail'
        _, _, rows, _, bounds = self.search({}, rule=outcome)
        self.assertIsNotNone(bounds['B']['passingRps'])
        self.assertLess(bounds['B']['passingRps'], 330)
        self.assertIsNone(bounds['B']['failingRps'])
        self.assertTrue(bounds['C']['bounded'])
        invalid = [p for _, p in rows if not p['valid']]
        self.assertTrue(invalid)
        self.assertTrue(all(p['repeat'] == 1 for p in invalid))

    def test_short_pass_that_fails_formally_is_never_imported_as_a_passing_bound(self):
        def outcome(p, phase):
            threshold = 450 if phase == 'probe' else 320
            return 'pass' if p['rate'] <= threshold else 'fail'
        _, _, _, _, bounds = self.search({}, rule=outcome)
        self.assertTrue(all(b['passingRps'] is not None and b['passingRps'] <= 320 for b in bounds.values()))

    def test_generator_limited_midpoint_is_not_retried_or_promoted_to_an_slo_failure(self):
        def outcome(p, phase):
            if phase == 'confirmation' and p['rate'] == 337: return 'limit'
            return 'pass' if p['rate'] <= (450 if phase == 'probe' else 320) else 'fail'
        _, calls, _, meta, bounds = self.search({}, rule=outcome, refine=6)
        for path in ('B', 'C'):
            self.assertEqual(1, sum(p['phase'] == 'confirmation' and p['path'] == path and p['rate'] == 337 for p in calls))
            self.assertEqual(300, bounds[path]['passingRps'])
            self.assertEqual(375, bounds[path]['failingRps'])
            self.assertIsNotNone(baseline_readiness(bounds[path]))
            self.assertEqual('generator-limited', meta['search']['paths'][path]['stopReason'])

    def test_time_budget_does_not_start_unfinishable_formal_blocks(self):
        _, calls, _, meta, bounds = self.search({'B': 330, 'C': 450}, budget=0)
        self.assertEqual([], calls)
        self.assertEqual('time-budget', meta['search']['stopReason'])
        self.assertTrue(all(b['passingRps'] is None for b in bounds.values()))

    def test_only_known_load_pressure_can_recover_after_data_and_drain_checks(self):
        value = {'loadErrors': [LOAD_LIMIT], 'correctnessErrors': [], 'unresolvedCount': 0}
        self.assertTrue(recoverable_load_limit(value))
        for change in ({'correctnessErrors': ['stock mismatch']}, {'unresolvedCount': 1},
                       {'loadErrors': ['Interrupted requests']}, {'loadErrors': ['k6 exit 1']}, {'loadErrors': []}):
            with self.subTest(change=change): self.assertFalse(recoverable_load_limit({**value, **change}))


if __name__ == '__main__': unittest.main()
