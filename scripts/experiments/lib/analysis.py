"""Offline reconciliation; no tokens, database connections, or injected load here."""
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def records(path):
    result = []
    for line in Path(path).read_text().splitlines():
        # k6 --log-format raw writes console messages without logfmt quoting.
        if not line.strip():
            continue
        value = json.loads(line)
        result.append(value)
    return result


def percentiles(values):
    values = sorted(values)
    def percentile(q):
        if not values:
            return None
        pos = (len(values) - 1) * q
        lo, hi = math.floor(pos), math.ceil(pos)
        return round(values[lo] + (values[hi] - values[lo]) * (pos - lo), 3)
    return {"samples": len(values), "p50": percentile(.5), "p95": percentile(.95), "p99": percentile(.99)}


def analyze(config, requests, commits, before, after, k6, cutoff_ms):
    starts = {r["requestId"]: r for r in requests if r["type"] == "start"}
    finishes = {r["requestId"]: r for r in requests if r["type"] == "finish"}
    users = {u["index"]: u for u in after["users"]}
    previous = {o["orderId"] for o in before["orders"]}
    new = {o["orderId"]: o for o in after["orders"] if o["orderId"] not in previous}
    by_user = {o["userId"]: o for o in new.values()}
    completed_events = {}
    for event in commits:
        if event["activityId"] == config["activityId"] and event["commitObservedAtMs"] <= cutoff_ms:
            completed_events.setdefault(event["orderId"], event)
    outcomes = Counter(r["outcome"] for r in finishes.values())
    outcomes["interrupted"] += len(starts.keys() - finishes.keys())
    accepted = [r for r in finishes.values() if r["outcome"] == "accepted"]
    errors = []
    unresolved, latencies, unexpected_commits = [], [], []
    origin = min((r["startedAtMs"] for r in starts.values()), default=cutoff_ms)
    injection_end = min((r["injectionEndMs"] for r in starts.values()), default=cutoff_ms)
    seconds = config["seconds"] + (config["baseSeconds"] if config["workload"] == "burst" else 0)
    timeline = defaultdict(lambda: {"started": 0, "accepted": 0, "completed": 0, "acceptedCompleted": 0})
    accepted_ids = {r["orderId"] for r in accepted}
    for r in starts.values():
        user = users.get(r["index"])
        if user is None:
            errors.append(f"Unknown user index {r['index']}")
            continue
        order = by_user.get(user["userId"])
        finish = finishes.get(r["requestId"])
        event = completed_events.get(order["orderId"]) if order else None
        if event:
            latency = event["commitObservedAtMs"] - r["startedAtMs"]
            if latency < 0:
                errors.append("Negative completion latency: clock/evidence mismatch")
            else:
                latencies.append(latency)
            if not finish or finish["outcome"] != "accepted":
                unexpected_commits.append({"requestId": r["requestId"], "orderId": order["orderId"]})
        elif finish and finish["outcome"] == "accepted":
            unresolved.append(r["requestId"])
        timeline[max(0, (r["startedAtMs"] - origin) // 1000)]["started"] += 1
    for r in accepted:
        order = new.get(r["orderId"])
        user = users[r["index"]]
        if order is None:
            errors.append(f"Accepted order missing: {r['orderId']}")
        elif order["userId"] != user["userId"] or order["status"] != 2:
            errors.append(f"Accepted order owner/status mismatch: {r['orderId']}")
        timeline[max(0, (r["finishedAtMs"] - origin) // 1000)]["accepted"] += 1
    for order_id, order in new.items():
        if order_id not in completed_events:
            errors.append(f"Commit evidence missing: {order_id}")
        if order["status"] != 2:
            errors.append(f"Unpaid order: {order_id}")
        if order_id in completed_events and completed_events[order_id]["userId"] != order["userId"]:
            errors.append(f"Commit event owner mismatch: {order_id}")
    for order_id, event in completed_events.items():
        if order_id not in previous:
            if order_id not in new:
                errors.append(f"Commit event without DB order: {order_id}")
            bucket = timeline[max(0, (event["commitObservedAtMs"] - origin) // 1000)]
            bucket["completed"] += 1
            bucket["acceptedCompleted"] += int(order_id in accepted_ids)
    logs = defaultdict(list)
    spent = Counter()
    for log in after["logs"]:
        spent[log["userId"]] += log["delta"]
        if log["bizType"] == 2:
            logs[log["orderId"]].append(log)
    for order in after["orders"]:
        linked = logs[order["orderId"]]
        if len(linked) != 1 or linked[0]["userId"] != order["userId"] or linked[0]["delta"] != -order["price"]:
            errors.append(f"Point log mismatch: {order['orderId']}")
    for user in users.values():
        if user["points"] != config["points"] + spent[user["userId"]]:
            errors.append(f"Point balance mismatch: index {user['index']}")
    for order_id in logs.keys() - {o["orderId"] for o in after["orders"]}:
        errors.append(f"Orphan consumption log: {order_id}")
    if len({o["userId"] for o in after["orders"]}) != len(after["orders"]):
        errors.append("More than one order per user/activity")
    if after["stock"] < 0 or before["stock"] - len(new) != after["stock"]:
        errors.append("DB stock conservation failed")
    if config["path"] != "A":
        if after["redisStock"] != after["stock"]:
            errors.append("Redis/DB stock mismatch at cutoff")
        expected_bought = {str(o["userId"]) for o in after["orders"]}
        if set(after["bought"]) - {"__warmup__"} != expected_bought:
            errors.append("Redis bought set/DB orders mismatch")
    # Detect accidental changes in workload composition, including interrupted runs.
    for r in starts.values():
        expected = "duplicate" if config["workload"] == "duplicate" and r["index"] % 10 != 9 else "accepted"
        if config["workload"] == "soldout":
            expected = "sold_out"
        finish = finishes.get(r["requestId"])
        if finish and finish["outcome"] in ("duplicate", "sold_out") and finish["outcome"] != expected:
            errors.append(f"Workload changed unexpectedly: {r['requestId']}")
    buckets = []
    pending = peak = 0
    total_accepted = total_completed_accepted = 0
    last_second = max(timeline, default=0)
    for second in range(last_second + 1):
        row = timeline[second]
        total_accepted += row["accepted"]
        total_completed_accepted += row["acceptedCompleted"]
        pending = max(0, total_accepted - total_completed_accepted)
        peak = max(peak, pending)
        buckets.append({"second": second, **row, "pendingAccepted": pending})
    dropped = k6.get("metrics", {}).get("dropped_iterations", {}).get("values", {}).get("count", 0)
    if dropped or len(starts) != config["scheduled"]:
        errors.append("Offered load not achieved (dropped/missing iterations)")
    if starts.keys() != finishes.keys():
        errors.append("Interrupted or unmatched request records")
    if not starts:
        errors.append("No requests recorded")
    if unresolved:
        errors.append("Accepted requests unfinished at cutoff")
    # Exact event ordering: a C order may commit before its PENDING response reaches k6.
    event_order = [(r["finishedAtMs"], "accepted", r["orderId"]) for r in accepted]
    event_order += [(e["commitObservedAtMs"], "completed", oid)
                    for oid, e in completed_events.items() if oid in accepted_ids]
    seen_accepted, seen_completed = set(), set()
    peak = 0
    for _, kind, oid in sorted(event_order):
        (seen_accepted if kind == "accepted" else seen_completed).add(oid)
        peak = max(peak, len(seen_accepted - seen_completed))
    response = {outcome: percentiles([r["responseMs"] for r in finishes.values() if r["outcome"] == outcome])
                for outcome in outcomes}
    infrastructure_errors = sum(outcomes[k] for k in ("timeout", "network_error", "internal_error", "unauthorized", "rate_limited", "interrupted"))
    last_commit = max((e["commitObservedAtMs"] for oid, e in completed_events.items() if oid not in previous), default=injection_end)
    return {
        "path": config["path"], "workload": config["workload"], "rate": config["rate"],
        "scheduled": config["scheduled"], "started": len(starts), "outcomes": dict(outcomes),
        "accepted": len(accepted), "completed": len(new.keys() & completed_events.keys()),
        "dbOrdersAtSnapshot": len(new), "unresolved": unresolved,
        "committedAfterResponseFailure": unexpected_commits, "dropped": dropped,
        "actualRequestRps": len(starts) / seconds, "acceptedRps": len(accepted) / seconds,
        "completionWindowRps": sum(1 for oid, e in completed_events.items()
                                   if oid not in previous and origin <= e["commitObservedAtMs"] <= injection_end) / seconds,
        "responseMs": response, "completionMs": percentiles(latencies),
        "completionCoverage": len(latencies) / len(starts) if starts else 0,
        "peakPendingAccepted": peak, "drainMs": max(0, last_commit - injection_end) if not unresolved else None,
        "cutoffMs": cutoff_ms, "infrastructureErrorRate": infrastructure_errors / len(starts) if starts else None,
        "correctnessErrors": sorted(set(errors)), "timeline": buckets,
    }


def save_result(directory, result):
    directory = Path(directory)
    write_json(directory / "result.json", result)
    with (directory / "timeline.csv").open("w") as file:
        writer = csv.DictWriter(file, fieldnames=["second", "started", "accepted", "completed", "acceptedCompleted", "pendingAccepted"])
        writer.writeheader()
        writer.writerows(result["timeline"])
    # Standalone vector curves, with separate scales for per-second flow and pending count.
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="880" height="510" viewBox="0 0 880 510">',
           '<rect width="880" height="510" fill="white"/>',
           '<g font-family="sans-serif" font-size="13" fill="#222">']
    rows = result["timeline"]
    panels = [("Requests per second", ("started", "accepted", "completed"), 45),
              ("Accepted orders awaiting completion", ("pendingAccepted",), 285)]
    colors = {"started": "#64748b", "accepted": "#2563eb", "completed": "#16a34a", "pendingAccepted": "#d97706"}
    for title, fields, top in panels:
        ymax = max([row[field] for row in rows for field in fields] or [1]) or 1
        svg.append(f'<text x="55" y="{top-15}">{title} (max scale {ymax})</text>')
        svg.append(f'<path d="M55 {top} V{top+150} H840" stroke="#aaa" fill="none"/>')
        for n, field in enumerate(fields):
            points = " ".join(f"{55+785*i/max(1,len(rows)-1):.1f},{top+150-150*row[field]/ymax:.1f}" for i, row in enumerate(rows))
            svg.append(f'<polyline points="{points}" fill="none" stroke="{colors[field]}" stroke-width="2"/>')
            svg.append(f'<text x="{55+230*n}" y="{top+175}" fill="{colors[field]}">{field}</text>')
        svg.append(f'<text x="770" y="{top+195}">{max(0,len(rows)-1)} seconds</text>')
    svg.append('</g></svg>')
    (directory / "curves.svg").write_text("\n".join(svg) + "\n")
    (directory / "report.md").write_text(
        f"# {result['path']} / {result['workload']} / {result['rate']} RPS\n\n"
        "![请求、受理、完成及待完成曲线](curves.svg)\n\n"
        "逐笔核对与分类指标见 [result.json](result.json)，原始曲线见 [timeline.csv](timeline.csv)。\n\n"
        f"完成延迟样本覆盖率：{result['completionCoverage']:.1%}。"
        f"截止时未完成受理请求：{len(result['unresolved'])}。\n\n"
        "数据/负载核对：" + ("通过。" if not result["correctnessErrors"] else "；".join(result["correctnessErrors"])) + "\n")
