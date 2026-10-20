import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from analysis import analyze, percentiles
from runner import matrix, parse_args


class AnalysisTest(unittest.TestCase):
    def fixture(self):
        config = dict(activityId=12, path="C", workload="steady", seconds=1, baseSeconds=0,
                      scheduled=1, rate=1, points=1000)
        before = dict(users=[dict(index=0, userId=7, points=1000)], orders=[], logs=[], stock=10, redisStock=10, bought=[])
        after = copy.deepcopy(before)
        after.update(stock=9, redisStock=9, bought=["7"],
                     orders=[dict(orderId="900", userId=7, price=100, status=2)],
                     logs=[dict(userId=7, orderId="900", delta=-100, bizType=2)])
        after["users"][0]["points"] = 900
        requests = [dict(type="start", requestId="steady:0", index=0, startedAtMs=1000, injectionEndMs=2000),
                    dict(type="finish", requestId="steady:0", index=0, startedAtMs=1000, finishedAtMs=1020,
                         outcome="accepted", responseMs=20, orderId="900")]
        commits = [dict(activityId=12, userId=7, orderId="900", commitObservedAtMs=2100)]
        return config, requests, commits, before, after

    def run_fixture(self, fixture, cutoff=3000):
        return analyze(*fixture, {}, cutoff)

    def test_acceptance_and_completion_are_separate_and_include_drain(self):
        result = self.run_fixture(self.fixture())
        self.assertEqual([], result["correctnessErrors"])
        self.assertEqual(1, result["accepted"])
        self.assertEqual(0, result["completionWindowRps"])
        self.assertEqual(1100, result["completionMs"]["p99"])
        self.assertEqual(100, result["drainMs"])
        self.assertEqual(1, result["peakPendingAccepted"])

    def test_unfinished_at_cutoff_not_zero_latency_or_success(self):
        fixture = self.fixture()
        result = self.run_fixture(fixture, cutoff=2050)
        self.assertEqual(["steady:0"], result["unresolved"])
        self.assertEqual(0, result["completed"])
        self.assertIsNone(result["completionMs"]["p99"])
        self.assertIsNone(result["drainMs"])
        self.assertEqual(0, result["completionCoverage"])

    def test_timeout_can_still_commit(self):
        fixture = self.fixture()
        fixture[1][1].update(outcome="timeout", orderId=None)
        result = self.run_fixture(fixture)
        self.assertEqual(0, result["accepted"])
        self.assertEqual(1, result["completed"])
        self.assertEqual([dict(requestId="steady:0", orderId="900")], result["committedAfterResponseFailure"])

    def test_point_logs_and_stock_are_verified(self):
        fixture = self.fixture()
        fixture[4]["logs"].append(copy.deepcopy(fixture[4]["logs"][0]))
        fixture[4]["stock"] = 8
        result = self.run_fixture(fixture)
        self.assertTrue(any("Point log mismatch" in error for error in result["correctnessErrors"]))
        self.assertIn("DB stock conservation failed", result["correctnessErrors"])

    def test_missing_request_and_dropped_load_are_invalid(self):
        fixture = self.fixture()
        fixture[0]["scheduled"] = 2
        fixture[1].pop()
        result = self.run_fixture(fixture)
        self.assertEqual(1, result["outcomes"]["interrupted"])
        self.assertIn("Offered load not achieved (dropped/missing iterations)", result["correctnessErrors"])

    def test_commit_before_response_does_not_create_fake_pending_peak(self):
        fixture = self.fixture()
        fixture[2][0]["commitObservedAtMs"] = 1010
        result = self.run_fixture(fixture)
        self.assertEqual(0, result["peakPendingAccepted"])

    def test_percentiles_missing_not_zero(self):
        self.assertIsNone(percentiles([])["p99"])

    def test_matrix_has_only_pairwise_plans_and_alternates_order(self):
        args = parse_args(["redis-filtering", "--rates", "10", "--workloads", "valid", "--repeats", "2"])
        points = list(matrix(args))
        self.assertEqual(["A", "B", "B", "A"], [p["path"] for p in points])
        self.assertTrue(all(p["scheduled"] == 600 for p in points))
        args = parse_args(["kafka-buffering", "--q", "100", "--workloads", "burst", "--repeats", "1"])
        self.assertEqual(["B", "C", "B", "C"], [p["path"] for p in matrix(args)])
        self.assertEqual(2500, next(matrix(args))["scheduled"])


if __name__ == "__main__":
    unittest.main()
