"""Fixed-start discovery and fresh formal confirmation; never consume historical evidence."""
import copy
import math

from protocol import capacities, matrix


def recoverable_load_limit(value):
    errors = value.get('loadErrors', [])
    allowed = ('Offered load not achieved', 'Injection timing does not follow the arrival schedule',
               'k6 exhausted its fixed VU pool (Insufficient VUs); arrivals were dropped')
    return (bool(errors) and not value.get('correctnessErrors') and not value.get('unresolvedCount')
            and all(error in allowed for error in errors))


def discovery_next(rows, start):
    if not rows:
        return start, None
    passed = [r['rate'] for r in rows if r['state'] == 'pass']
    failed = [r['rate'] for r in rows if r['state'] == 'fail']
    limited = [r['rate'] for r in rows if r['state'] == 'generator-limited']
    lower = max(passed, default=None)
    if lower is not None and any(rate < lower for rate in failed):
        return None, 'probe-order-conflict'
    ceilings = [r for r in failed+limited if lower is None or r > lower]
    upper = min(ceilings, default=None)
    if lower is not None and upper is not None:
        if upper-lower <= max(1, lower*.1):
            return None, 'generator-limited' if upper in limited else 'probe-bracket'
        candidate = (lower+upper)//2
    elif lower is not None:
        candidate = math.ceil(lower*1.25)
    else:
        candidate = max(1, min(r['rate'] for r in rows)*4//5)
    if candidate in {r['rate'] for r in rows}:
        return None, 'probe-resolution-limit'
    return candidate, None


def run(args, execute, results, meta, remaining, checkpoint):
    """execute(point, phase) retains all evidence and enforces safety before returning."""
    paths = ('A', 'B') if args.plan == 'redis-filtering' else ('B', 'C')
    states = {path: {'probes': [], 'probeStopReason': None, 'stopReason': None} for path in paths}
    search = {'strategy': 'fixed-start-probe-confirm', 'fixedStartRps': args.rates[0],
              'probeSeconds': args.probe_seconds, 'probeStepsPerPath': args.probe_steps,
              'budgetMinutes': args.search_minutes, 'paths': states,
              'formalBatches': [], 'comparisonAnchorRps': None, 'stopReason': None}
    meta['search'] = search
    checkpoint()

    def point(path, rate, repeat=1, probe=False):
        selected = copy.copy(args)
        if probe:
            selected.validation_only, selected.seconds, selected.repeats = True, args.probe_seconds, 1
        value = next(p for p in matrix(selected, [rate]) if p['path'] == path and p['repeat'] == repeat)
        value['phase'] = 'probe' if probe else 'confirmation'
        return value

    # Reserve at least one complete formal block per path while discovering candidates.
    reserve = sum(args.repeats*(point(p, args.rates[0])['seconds']+args.request_timeout+30) for p in paths)
    for _ in range(args.probe_steps):
        active = False
        for path in paths:
            state = states[path]
            if state['probeStopReason']:
                continue
            rate, reason = discovery_next(state['probes'], args.rates[0])
            if rate is None:
                state['probeStopReason'] = reason
                continue
            config = point(path, rate, probe=True)
            if remaining() < config['seconds']+args.request_timeout+30+reserve:
                state['probeStopReason'] = 'probe-time-budget'
                continue
            active = True
            value = execute(config, 'probe')
            if recoverable_load_limit(value):
                outcome = 'generator-limited'
            elif value['valid']:
                outcome = 'pass' if value['sloPassed'] else 'fail'
            else:
                raise RuntimeError('Invalid discovery evidence cannot guide capacity search')
            state['probes'].append({'rate': rate, 'state': outcome, 'scope': 'validation',
                                    'qualifiedForCapacity': False, 'directory': value.get('evidenceDirectory')})
            checkpoint()
        if not active:
            break
    for state in states.values():
        state['probeStopReason'] = state['probeStopReason'] or 'probe-step-limit'
    if any(s['probeStopReason'] == 'probe-time-budget' for s in states.values()):
        search['stopReason'] = 'time-budget'

    def confirm(candidates, reason):
        # Interleave paths by repeat even when their candidate rates differ.
        points = [point(path, candidates[path], repeat) for repeat in range(1, args.repeats+1)
                  for path in (paths if repeat % 2 else paths[::-1]) if path in candidates]
        if not points:
            return True
        if remaining() < sum(p['seconds']+args.request_timeout+30 for p in points):
            search['stopReason'] = 'time-budget'
            checkpoint()
            return False
        search['formalBatches'].append({'reason': reason, 'rates': dict(candidates)})
        blocked = set()
        for config in points:
            if config['path'] in blocked:
                continue
            if remaining() < config['seconds']+args.request_timeout+30:
                search['stopReason'] = 'time-budget'
                checkpoint()
                return False
            value = execute(config, 'confirmation')
            if recoverable_load_limit(value):
                blocked.add(config['path'])
                states[config['path']]['stopReason'] = 'generator-limited'
            checkpoint()
        return True

    primary, upper = {}, {}
    for path, state in states.items():
        passed = [r['rate'] for r in state['probes'] if r['state'] == 'pass']
        failed = [r['rate'] for r in state['probes'] if r['state'] == 'fail']
        if passed:
            primary[path] = max(passed)
            if failed:
                upper[path] = min(failed)
        elif failed:
            primary[path] = min(failed)
        else:
            state['stopReason'] = 'no-valid-probe'
    # Confirm a shared load with adjacent paired rounds before the paths diverge.
    # This costs the same extra block as filling a missing comparison at the end,
    # while reducing time/data-growth drift between the paired measurements.
    both_passed = all(any(r['state'] == 'pass' for r in state['probes']) for state in states.values())
    initial = dict.fromkeys(paths, min(primary.values())) if both_passed else primary
    if not confirm(initial, 'paired-anchor-and-pass' if both_passed else 'confirm-discovery-pass'):
        return
    bounds = capacities(args, results)
    near_boundary = {path: rate for path, rate in primary.items() if rate != initial[path]
                     and bounds[path]['passingRps'] is not None and states[path]['stopReason'] != 'generator-limited'}
    if not confirm(near_boundary, 'confirm-discovery-pass'):
        return
    # Do not revisit a higher short-failure candidate after formal injection already saturated.
    upper = {path: rate for path, rate in upper.items()
             if states[path]['stopReason'] != 'generator-limited' and not any(
                 r['state'] == 'fail' and r['rate'] <= rate for r in capacities(args, results)[path]['observations'])}
    if not confirm(upper, 'confirm-discovery-failure'):
        return

    for _ in range(args.refine_steps):
        candidates = {}
        bounds = capacities(args, results)
        for path, bound in bounds.items():
            if bound['nonMonotonic']:
                states[path]['stopReason'] = 'non-monotonic-formal-results'
                continue
            passed, failed = bound['passingRps'], bound['failingRps']
            observed = {r['rate'] for r in bound['observations']}
            if passed is not None and states[path]['stopReason'] == 'generator-limited':
                continue
            if bound['bounded']:
                if failed-passed > max(1, passed*.1):
                    candidate = (passed+failed)//2
                    if candidate not in observed:
                        candidates[path] = candidate
                continue
            failures = [r['rate'] for r in bound['observations'] if r['state'] == 'fail']
            invalid = [r['rate'] for r in bound['observations'] if r['state'] == 'invalid']
            # A short pass may fail a full minute on this machine. Find a fresh lower point.
            if passed is None and failures+invalid:
                ceiling = min(failures+invalid)
                lower_probes = [r['rate'] for r in states[path]['probes']
                                if r['state'] == 'pass' and r['rate'] < ceiling and r['rate'] not in observed]
                candidate = max(lower_probes, default=max(1, ceiling*4//5))
                if candidate not in observed:
                    candidates[path] = candidate
        if not candidates or not confirm(candidates, 'formal-refinement'):
            break

    # Keep a common, freshly confirmed load for the existing paired latency comparison.
    bounds = capacities(args, results)
    if all(b['passingRps'] and not b['nonMonotonic'] for b in bounds.values()):
        shared = set.intersection(*({o['rate'] for o in b['observations'] if o['state'] == 'pass'} for b in bounds.values()))
        anchor = max(shared) if shared else min(b['passingRps'] for b in bounds.values())
        candidates = {path: anchor for path, b in bounds.items()
                      if anchor not in {o['rate'] for o in b['observations']}}
        if confirm(candidates, 'paired-comparison-anchor'):
            search['comparisonAnchorRps'] = anchor
    for path, bound in capacities(args, results).items():
        if bound['bounded'] and bound['failingRps']-bound['passingRps'] <= max(1, bound['passingRps']*.1):
            states[path]['stopReason'] = 'bounds-confirmed'
        elif not states[path]['stopReason']:
            states[path]['stopReason'] = ('generator-limited' if any(r['state'] == 'generator-limited' for r in states[path]['probes'])
                                          else 'no-qualified-bound')
    search['stopReason'] = search['stopReason'] or ('bounds-resolved' if all(s['stopReason'] == 'bounds-confirmed' for s in states.values())
                                                   else 'inconclusive')
    checkpoint()
