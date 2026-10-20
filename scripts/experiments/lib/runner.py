#!/usr/bin/env python3
"""Two isolated local experiments. Requires only Python's standard library and k6/Docker."""
import argparse
import base64
from collections import defaultdict
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.request

from analysis import analyze, percentiles, records, save_result, write_json

ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / ".local/experiments"
POINTS = 10_000_000
PRICE = 100


def command(args, *, data=None, timeout=45, env=None):
    result = subprocess.run(args, input=data, text=True, capture_output=True, timeout=timeout, env=env, cwd=ROOT)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout


def compose(*args):
    return ["docker", "compose", "--project-name", "shanpu", "--env-file", str(ROOT / ".env"),
            "--file", str(ROOT / "docker-compose.yml"), *args]


def sql(statement):
    return command(compose("exec", "-T", "-e", "MYSQL_PWD", "mysql", "mysql", "-uroot", "-N", "-B",
                           "--default-character-set=utf8mb4", os.environ["MYSQL_DATABASE"]),
                   data=statement, env={**os.environ, "MYSQL_PWD": os.environ["MYSQL_ROOT_PASSWORD"]})


def redis(*args, data=None):
    return command(compose("exec", "-T", "-e", "REDISCLI_AUTH", "redis", "redis-cli", "--raw", *map(str, args)),
                   data=data, env={**os.environ, "REDISCLI_AUTH": os.environ["REDIS_PASSWORD"]}).strip()


def api(path, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"http://127.0.0.1:{os.environ['BACKEND_PORT']}/api{path}",
                                     headers=headers, data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(request, timeout=10) as response:
        value = json.load(response)
    if "code" in value:
        if value["code"] != 0:
            raise RuntimeError(f"API {path} returned code {value['code']}")
        return value.get("data")
    return value


def token_for(uid, username):
    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()
    now = int(time.time())
    head = encode(b'{"alg":"HS256","typ":"JWT"}')
    body = encode(json.dumps({"uid": uid, "uname": username, "iat": now, "exp": now + 86400}, separators=(",", ":")).encode())
    payload = f"{head}.{body}"
    signature = encode(hmac.new(os.environ["JWT_SECRET"].encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def session_commands(users):
    output = []
    for user in users:
        digest = hashlib.sha256(user["token"].encode()).hexdigest()[:32]
        args = ["SETEX", f"shanpu:user:session:{digest}", "86400", str(user["userId"])]
        output.append(f"*{len(args)}\r\n" + "".join(f"${len(a.encode())}\r\n{a}\r\n" for a in args))
    return "".join(output)


def create_users(prefix, count):
    for start in range(0, count, 1000):
        # Prefix is generated from hex digits. No external strings enter fixture SQL.
        values = ",".join(f"('{prefix}_{i}','disabled-experiment-login','实验用户',{POINTS})"
                          for i in range(start, min(start + 1000, count)))
        sql(f"INSERT INTO t_user(username,password,nickname,points) VALUES {values};")
    rows = sql(f"SELECT id,username FROM t_user WHERE username REGEXP '^{prefix}_[0-9]+$';")
    users = []
    for line in rows.splitlines():
        uid, username = line.split("\t")
        users.append({"index": int(username.rsplit("_", 1)[1]), "userId": int(uid), "token": token_for(int(uid), username)})
    users.sort(key=lambda u: u["index"])
    if len(users) != count:
        raise RuntimeError("User pool creation incomplete")
    redis("--pipe", data=session_commands(users))
    return users


def lag():
    output = command(compose("exec", "-T", "kafka", "/opt/kafka/bin/kafka-consumer-groups.sh",
                             "--bootstrap-server", "kafka:9092", "--describe", "--group", "shanpu-seckill-consumer"))
    rows = [line.split() for line in output.splitlines() if "shanpu.seckill.order" in line]
    partitions = [row for row in rows if len(row) >= 6 and row[1] == "shanpu.seckill.order"]
    for row in partitions:
        if row[3] == "-" and row[4] == "0" and row[5] == "-":
            row[5] = "0"  # A genuinely empty partition may have no committed offset yet.
    if len(partitions) != 3 or any(not row[5].isdigit() for row in partitions):
        raise RuntimeError("Cannot confirm Kafka offsets for all three partitions")
    return {row[2]: int(row[5]) for row in partitions}


def require_idle(token):
    metrics = api("/admin/experiments/metrics", token)
    if not metrics["enabled"] or metrics.get("writeErrors"):
        raise RuntimeError("Experiment journal disabled or unhealthy; start backend with dev,experiment")
    if metrics.get("settings", {}).get("database") != os.environ["MYSQL_DATABASE"]:
        raise RuntimeError("Backend and runner target different databases")
    if any(value != "-" for key, value in metrics.get("settings", {}).items() if key.endswith(".cron")):
        raise RuntimeError("Scheduled jobs must be disabled during measurement")
    if metrics.get("poolActive", 0) or metrics.get("poolWaitThreads", 0):
        raise RuntimeError("Database requests still active; refusing to prepare the next point")
    if any(lag().values()):
        raise RuntimeError("Kafka still has in-flight messages; preserving data")
    # Even republished DLQ entries require explicit review: lag=0 alone is insufficient.
    if int(sql("SELECT COUNT(*) FROM t_order_dlq_log;").strip()):
        raise RuntimeError("DLQ evidence exists; resolve it before starting another point")
    return metrics


def snapshot(config):
    aid, prefix = config["activityId"], config["userPrefix"]
    users = []
    for row in sql(f"SELECT id,username,points FROM t_user WHERE username REGEXP '^{prefix}_[0-9]+$';").splitlines():
        uid, username, points = row.split("\t")
        users.append({"index": int(username.rsplit("_", 1)[1]), "userId": int(uid), "points": int(points)})
    orders = []
    for row in sql(f"SELECT order_id,user_id,price,status FROM t_order WHERE activity_id={aid};").splitlines():
        oid, uid, price, status = row.split("\t")
        orders.append({"orderId": oid, "userId": int(uid), "price": int(price), "status": int(status)})
    logs = []
    for row in sql(f"SELECT l.user_id,l.biz_ref,l.delta,l.biz_type FROM t_point_log l JOIN t_user u ON u.id=l.user_id "
                   f"WHERE u.username REGEXP '^{prefix}_[0-9]+$';").splitlines():
        uid, oid, delta, kind = row.split("\t")
        logs.append({"userId": int(uid), "orderId": oid, "delta": int(delta), "bizType": int(kind)})
    return {"users": users, "orders": orders, "logs": logs,
            "stock": int(sql(f"SELECT total_stock FROM t_seckill_activity WHERE id={aid};").strip()),
            "redisStock": int(redis("GET", f"shanpu:seckill:stock:{aid}")) if config["path"] != "A" else None,
            "bought": redis("SMEMBERS", f"shanpu:seckill:bought:{aid}").splitlines() if config["path"] != "A" else []}


def prepare(config, product_id, private, directory, token):
    users = create_users(config["userPrefix"], config["scheduled"] + 1)
    private.mkdir(parents=True, exist_ok=True)
    private.chmod(0o700)
    write_json(private / "users.json", users)
    # Separate products/activities per suite/point; demo fixtures are never overwritten.
    stock = 0 if config["workload"] == "soldout" else config["scheduled"] + 100
    prepared_count = sum(1 for i in range(config["scheduled"]) if i % 10 != 9) if config["workload"] == "duplicate" else 0
    tier = {"A": 1, "B": 2, "C": 3}[config["path"]]
    aid = int(sql(f"INSERT INTO t_seckill_activity(product_id,title,tier,seckill_price,total_stock,start_at,end_at,status) "
                  f"VALUES ({product_id},'{config['userPrefix']}',{tier},{PRICE},{stock+prepared_count},NOW()-INTERVAL 1 HOUR,NOW()+INTERVAL 1 DAY,1); "
                  "SELECT LAST_INSERT_ID();").strip())
    config["activityId"] = aid
    write_json(directory / "point.json", config)
    write_json(private / "point.json", config)
    if config["workload"] == "duplicate":
        prefix = config["userPrefix"]
        # Nine out of each ten scheduled users already own a real paid order.
        condition = f"u.username REGEXP '^{prefix}_[0-9]+$' AND CAST(SUBSTRING_INDEX(u.username,'_',-1) AS UNSIGNED)<{config['scheduled']} "
        condition += "AND MOD(CAST(SUBSTRING_INDEX(u.username,'_',-1) AS UNSIGNED),10)<>9"
        sql(f"START TRANSACTION; INSERT INTO t_order(order_id,user_id,product_id,activity_id,source,price,status,paid_at) "
            f"SELECT CONCAT('prep',u.id,'x',{aid}),u.id,{product_id},{aid},{tier+1},{PRICE},2,NOW() FROM t_user u WHERE {condition}; "
            f"UPDATE t_user u SET points=points-{PRICE} WHERE {condition}; "
            f"INSERT INTO t_point_log(user_id,delta,balance_after,biz_type,biz_ref) "
            f"SELECT o.user_id,-o.price,u.points,2,o.order_id FROM t_order o JOIN t_user u ON u.id=o.user_id WHERE o.activity_id={aid}; "
            f"UPDATE t_seckill_activity SET total_stock=total_stock-{prepared_count} WHERE id={aid}; "
            "COMMIT;")
    if config["path"] != "A":
        redis("SET", f"shanpu:seckill:stock:{aid}", stock)
        bought = [str(u["userId"]) for u in users[:-1] if config["workload"] == "duplicate" and u["index"] % 10 != 9]
        for offset in range(0, len(bought), 1000):
            redis("SADD", f"shanpu:seckill:bought:{aid}", *bought[offset:offset+1000])
    # Warm a spare user and the real transaction path; its order is baseline evidence only.
    warm = api(f"/seckills/{aid}/grab", users[-1]["token"], {}) if stock else None
    if warm:
        deadline = time.monotonic() + 60
        while sql(f"SELECT COUNT(*) FROM t_order WHERE order_id='{warm['orderId']}' AND status=2;").strip() != "1":
            if time.monotonic() > deadline:
                raise RuntimeError("Warmup order did not complete")
            time.sleep(.2)
    else:
        api(f"/seckills/{aid}")
    require_idle(token)
    write_json(directory / "before.json", snapshot(config))


def mysql_counters():
    names = "'Com_commit','Com_rollback','Com_insert','Com_update','Com_select','Innodb_row_lock_waits','Innodb_row_lock_time','Threads_running'"
    return {k: int(v) for k, v in (row.split("\t") for row in sql(f"SHOW GLOBAL STATUS WHERE Variable_name IN ({names});").splitlines())}


def container_stats():
    output = command(["docker", "stats", "--no-stream", "--format", "{{json .}}"])
    return [row for line in output.splitlines() for row in [json.loads(line)] if row["Name"].startswith("shanpu-")]


class Sampler:
    def __init__(self, directory, token):
        self.directory, self.token = directory, token
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        with (self.directory / "samples.jsonl").open("a") as file:
            sample_number = 0
            while not self.stop.is_set():
                sample = {"timeMs": int(time.time() * 1000)}
                for name, fn in (
                    ("backend", lambda: api("/admin/experiments/metrics", self.token)),
                    ("mysql", mysql_counters),
                    ("containers", container_stats),
                    ("host", lambda: {"loadavg": Path("/proc/loadavg").read_text().strip(),
                                      "cpu": Path("/proc/stat").read_text().splitlines()[0],
                                      "diskstats": Path("/proc/diskstats").read_text()}),
                ):
                    try:
                        sample[name] = fn()
                    except Exception as error:
                        sample[name + "Error"] = str(error)
                if sample_number % 2 == 0:
                    try:
                        sample["kafkaLag"] = lag()
                    except Exception as error:
                        sample["kafkaLagError"] = str(error)
                file.write(json.dumps(sample, ensure_ascii=False) + "\n")
                file.flush()
                sample_number += 1
                self.stop.wait(2)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=60)
        if self.thread.is_alive():
            raise RuntimeError("Resource sampler failed to stop")


def delta(before, after):
    result = {}
    for key in ("syncAttempts", "asyncAttempts", "events", "writeErrors", "poolWaitCount", "poolWaitMs", "gcTimeMs", "processCpuTimeNs"):
        if key in before and key in after:
            result[key] = after[key] - before[key]
    result["sqlCalls"] = {k: value - before["sqlCalls"].get(k, 0) for k, value in after["sqlCalls"].items()}
    return result


def measure(config, private, directory, token, args):
    before_metrics = require_idle(token)
    journal_path = Path(before_metrics["journal"])
    journal_offset = journal_path.stat().st_size if journal_path.exists() else 0
    write_json(directory / "backend-before.json", before_metrics)
    write_json(directory / "mysql-before.json", mysql_counters())
    relative = private.relative_to(RUNTIME)
    container_name = "shanpu-experiment-" + config["userPrefix"]
    invocation = compose("--profile", "experiments", "run", "--rm", "--name", container_name, "--user", f"{os.getuid()}:{os.getgid()}",
                         "-v", f"{directory}:/results:rw", "-e", f"POINT_CONFIG=/runtime/{relative}/point.json",
                         "-e", f"USER_FILE=/runtime/{relative}/users.json", "k6", "run", "--log-format", "raw",
                         "--console-output", "/results/requests.jsonl", f"/experiments/{config['plan']}.js")
    started = time.monotonic()
    with Sampler(directory, token), (directory / "k6.out").open("w") as output:
        process = subprocess.Popen(invocation, stdout=output, stderr=subprocess.STDOUT, cwd=ROOT)
        try:
            process.wait(timeout=config["seconds"] + config["baseSeconds"] + config["requestTimeout"] + 120)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    command(["docker", "stop", "--time", str(config["requestTimeout"] + 2), container_name],
                            timeout=config["requestTimeout"] + 30)
                finally:
                    try:
                        process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
        requests = records(directory / "requests.jsonl")
        scheduled_end = min((r["injectionEndMs"] for r in requests if r["type"] == "start"), default=int(time.time()*1000))
        # The drain deadline is relative to the end of injection, not to slow HTTP responses.
        deadline_ms = scheduled_end + args.drain_seconds * 1000
        finishes = [r for r in requests if r["type"] == "finish" and r["outcome"] == "accepted"]
        accepted = {r["orderId"] for r in finishes}
        while int(time.time()*1000) < deadline_ms:
            events = read_journal(journal_path, journal_offset, config["activityId"])
            backend = api("/admin/experiments/metrics", token)
            if accepted <= {e["orderId"] for e in events} and not any(lag().values()) and not backend.get("poolActive", 0):
                break
            time.sleep(1)
        cutoff_ms = min(int(time.time()*1000), deadline_ms)
        after = snapshot(config)
        events = read_journal(journal_path, journal_offset, config["activityId"])
        write_json(directory / "after.json", after)
        write_json(directory / "commits.json", events)
        after_metrics = api("/admin/experiments/metrics", token)
        write_json(directory / "backend-after.json", after_metrics)
        write_json(directory / "mysql-after.json", mysql_counters())
        write_json(directory / "kafka-final.json", lag())
    summary = json.loads((directory / "k6-summary.json").read_text())
    result = analyze(config, requests, events, json.loads((directory / "before.json").read_text()), after, summary, cutoff_ms)
    result["k6ExitCode"] = process.returncode
    result["wallSeconds"] = round(time.monotonic() - started, 3)
    result["backendDelta"] = delta(before_metrics, after_metrics)
    if (result["backendDelta"].get("writeErrors", 0) or after_metrics["events"] < before_metrics["events"]
            or before_metrics.get("processStartedAtMs") != after_metrics.get("processStartedAtMs")):
        result["correctnessErrors"].append("Backend journal failed or backend restarted")
    if (before_metrics["settings"] != after_metrics["settings"]
            or before_metrics["localCacheEnabled"] != after_metrics["localCacheEnabled"]):
        result["correctnessErrors"].append("Backend settings changed during measurement")
    if process.returncode:
        result["correctnessErrors"].append("k6 exited with a non-zero status")
    if any(json.loads((directory / "kafka-final.json").read_text()).values()):
        result["correctnessErrors"].append("Kafka not drained at cutoff")
    if int(sql("SELECT COUNT(*) FROM t_order_dlq_log;").strip()):
        result["correctnessErrors"].append("DLQ orders require review")
    result["sloPassed"] = not result["correctnessErrors"] and (result["infrastructureErrorRate"] or 0) <= args.max_error_rate
    expected_outcomes = ("sold_out",) if config["workload"] == "soldout" else ("accepted", "duplicate") if config["workload"] == "duplicate" else ("accepted",)
    response_samples = [r["responseMs"] for r in requests if r["type"] == "finish" and r["outcome"] in expected_outcomes]
    result["sloPassed"] &= (percentiles(response_samples)["p99"] or float("inf")) <= args.response_p99_ms
    if config["plan"] == "kafka-buffering":
        result["sloPassed"] &= (result["completionMs"]["p99"] or float("inf")) <= args.completion_p99_ms and result["completionCoverage"] == 1
    save_result(directory, result)
    return result


def read_journal(path, offset, activity):
    if not path.exists():
        return []
    with path.open() as file:
        file.seek(offset)
        # A concurrent append may leave a partial final record; the next read will retry it.
        lines = file.read().splitlines(keepends=True)
    return [event for line in lines if line.endswith("\n")
            for event in [json.loads(line)] if event["activityId"] == activity]


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", choices=["redis-filtering", "kafka-buffering"])
    parser.add_argument("--rates", help="Comma-separated fixed input RPS; overrides Q-based steady rates")
    parser.add_argument("--q", type=positive, help="Measured stable B completion capacity, orders/second")
    parser.add_argument("--calibrate", action="store_true", help="Kafka plan: measure B only to propose Q")
    parser.add_argument("--workloads", help="redis: valid,duplicate,soldout; kafka: steady,burst")
    parser.add_argument("--seconds", type=positive, default=60)
    parser.add_argument("--repeats", type=positive, default=3)
    parser.add_argument("--vus", type=positive, default=100)
    parser.add_argument("--burst-multipliers", default="2,5")
    parser.add_argument("--burst-seconds", type=positive, default=10)
    parser.add_argument("--base-seconds", type=positive, default=10)
    parser.add_argument("--request-timeout", type=positive, default=10)
    parser.add_argument("--drain-seconds", type=positive, default=120)
    parser.add_argument("--response-p99-ms", type=positive, default=500)
    parser.add_argument("--completion-p99-ms", type=positive, default=5000)
    parser.add_argument("--max-error-rate", type=float, default=.001)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not 0 <= args.max_error_rate <= 1:
        parser.error("--max-error-rate must be between 0 and 1")
    if args.calibrate and args.plan != "kafka-buffering":
        parser.error("--calibrate applies only to kafka-buffering")
    if args.plan == "kafka-buffering" and not args.calibrate and not args.q:
        parser.error("Provide measured --q, or run --calibrate first")
    try:
        args.rates = [positive(v) for v in args.rates.split(",")] if args.rates else (
            [25, 50, 100, 200] if args.calibrate else
            sorted({max(1, int(args.q * f)) for f in (.5, .8, 1.2)}) if args.q else [50, 100, 200])
        args.burst_multipliers = [positive(v) for v in args.burst_multipliers.split(",")]
    except (ValueError, argparse.ArgumentTypeError) as error:
        parser.error(str(error))
    allowed = ["valid", "duplicate", "soldout"] if args.plan == "redis-filtering" else ["steady", "burst"]
    args.workloads = args.workloads.split(",") if args.workloads else (["steady"] if args.calibrate else allowed)
    if not args.workloads or any(w not in allowed for w in args.workloads):
        parser.error(f"--workloads must select from {','.join(allowed)}")
    if args.calibrate and args.workloads != ["steady"]:
        parser.error("Calibration uses only steady valid requests")
    return args


def matrix(args):
    paths = ["A", "B"] if args.plan == "redis-filtering" else (["B"] if args.calibrate else ["B", "C"])
    for repeat in range(1, args.repeats + 1):
        for workload in args.workloads:
            rates = [args.q * v for v in args.burst_multipliers] if workload == "burst" else args.rates
            for rate in rates:
                for path in (paths if repeat % 2 else paths[::-1]):
                    seconds = args.burst_seconds if workload == "burst" else args.seconds
                    base_rate = max(1, int((args.q or rate) * .5))
                    base_seconds = args.base_seconds if workload == "burst" else 0
                    yield {"plan": args.plan, "repeat": repeat, "workload": workload, "path": path,
                           "rate": rate, "seconds": seconds, "baseRate": base_rate, "baseSeconds": base_seconds,
                           "vus": args.vus, "requestTimeout": args.request_timeout, "points": POINTS,
                           "scheduled": rate * seconds + base_rate * base_seconds}


def manifest(args):
    tracked = command(["git", "ls-files", "-co", "--exclude-standard"]).splitlines()
    sources = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in tracked
               if (ROOT / name).is_file() and (name.startswith(("backend/", "scripts/", "deploy/")) or name == "docker-compose.yml")}
    return {"plan": args.plan, "calibration": args.calibrate, "status": "running", "startedAtMs": int(time.time()*1000),
            "database": os.environ["MYSQL_DATABASE"],
            "gitCommit": command(["git", "rev-parse", "HEAD"]).strip(),
            "gitStatus": command(["git", "status", "--porcelain"]), "sourceSha256": sources,
            "parameters": vars(args), "platform": platform.platform(), "cpuCount": os.cpu_count(),
            "memory": Path("/proc/meminfo").read_text().splitlines()[0], "points": [],
            "limitations": ["Local single broker/replica; no durability or high-availability claim",
                             "Completion time observed immediately after DB commit; includes instrumentation delay",
                             "k6, backend, DB and brokers share this host; sampler overhead is present",
                             "Global MySQL counters include observer queries; mapper counters are separate",
                             "Business completion and request logging add the same observation steps to both paths"]}


def report(directory, meta, results):
    lines = [f"# {meta['plan']} 实验", "", f"状态：{meta['status']}。数据现场保留。", "",
             "PAID 表示同步完成；PENDING 只表示 Kafka 已确认受理。完成吞吐只统计测量窗口内提交的新增订单。",
             "完成延迟同时报告覆盖率；截止时未完成订单保留，不按零延迟处理。", "",
             "| 测量点 | 响应状态 | 实际请求/s | 受理/s | 窗口完成/s | 响应 p99 ms | 完成 p99 ms | 完成覆盖率 | 排空 ms | SLO | 数据/负载核对 |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|"]
    groups = defaultdict(list)
    def fmt(value):
        return "NA" if value is None else f"{value:.3f}"
    for name, value in results:
        p99 = value["responseMs"].get("accepted", {}).get("p99")
        lines.append(f"| [{name}]({name}/report.md) | {'PENDING' if value['path']=='C' else 'PAID'} | "
                     f"{fmt(value['actualRequestRps'])} | {fmt(value['acceptedRps'])} | {fmt(value['completionWindowRps'])} | "
                     f"{fmt(p99)} | {fmt(value['completionMs']['p99'])} | {value['completionCoverage']:.1%} | {fmt(value['drainMs'])} | "
                     f"{'通过' if value['sloPassed'] else '未通过'} | {'通过' if not value['correctnessErrors'] else '失败'} |")
        groups[(value["workload"], value["rate"], value["path"])].append(value)
    lines += ["", "## 重复轮次", "", "只汇总数据及输入核对通过的轮次；波动列给出最小值与最大值。SLO 未通过的有效轮次仍保留。", "",
              "| 档位 | 输入 RPS | 链路 | 有效/总轮数 | 完成/s 中位数 | 完成/s 范围 | DB 事务尝试中位数 |", "|---|---:|---|---:|---:|---|---:|"]
    for (workload, rate, path), values in groups.items():
        valid = [v for v in values if not v["correctnessErrors"]]
        rates = [v["completionWindowRps"] for v in valid]
        attempts = [v["backendDelta"].get("syncAttempts", 0) + v["backendDelta"].get("asyncAttempts", 0) for v in valid]
        span = f"{fmt(min(rates))}～{fmt(max(rates))}" if rates else "NA"
        lines.append(f"| {workload} | {rate} | {path} | {len(valid)}/{len(values)} | {fmt(percentiles(rates)['p50'])} | {span} | {fmt(percentiles(attempts)['p50'])} |")
    if meta["calibration"]:
        passing = [rate for (_, rate, _), values in groups.items()
                   if len(values) == meta["parameters"]["repeats"] and all(v["sloPassed"] for v in values)]
        meta["proposedQ"] = max(passing, default=None)
        lines += ["", f"B 稳定容量候选 Q：{meta['proposedQ'] or '尚未找到'}。需确认更长时间运行及资源曲线后采用。"]
    lines += ["", "## 证据与边界", "", "- 环境、源码指纹、参数与限制见 [manifest.json](manifest.json)。",
              "- 各点保留 requests.jsonl、commits.json、before/after.json、k6-summary.json、timeline.csv 与资源采样。",
              "- 固定输入未达到、状态变化、缺少提交证据或未排空的轮次标为失败，不能用于容量提升结论。",
              "- summary 表的响应分位数针对受理请求；重复/售罄延迟见各点 result.json 的 responseMs。",
              "- 所有例示默认 SLO 都可通过参数配置，不能跨不同 SLO 比较容量。"]
    (directory / "report.md").write_text("\n".join(lines) + "\n")


def main(argv=None):
    args = parse_args(argv)
    points = list(matrix(args))
    if args.dry_run:
        print(json.dumps({"parameters": vars(args), "measurements": points}, ensure_ascii=False, indent=2))
        return 0
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with (RUNTIME / "experiment.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        health = api("/health")
        if any(health.get(key) != "UP" for key in ("status", "mysql", "redis", "kafka")):
            raise RuntimeError("Backend and dependencies must be healthy")
        name = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        directory = ROOT / "docs/experiments" / args.plan / name
        directory.mkdir(parents=True)
        private = RUNTIME / name
        private.mkdir(mode=0o700)
        meta, results = manifest(args), []
        write_json(directory / "manifest.json", meta)
        print(f"报告目录：{directory}", flush=True)
        try:
            meta["adminPrefix"] = "x" + secrets.token_hex(6)
            write_json(directory / "manifest.json", meta)
            admin = create_users(meta["adminPrefix"], 1)[0]
            write_json(private / "admin.json", [admin])
            metrics = require_idle(admin["token"])
            write_json(directory / "backend-settings.json", metrics)
            config = json.loads(command(compose("--profile", "experiments", "config", "--format", "json")))
            write_json(directory / "resources.json", {key: {k: service[k] for k in ("image", "deploy", "ports", "network_mode") if k in service}
                                                      for key, service in config["services"].items()})
            (directory / "images.txt").write_text(command(["docker", "inspect", "--format", "{{.Name}} {{.Config.Image}} {{.Image}}",
                                                          "shanpu-mysql", "shanpu-redis", "shanpu-kafka"]))
            product_id = int(sql(f"INSERT INTO t_product(title,price,stock,status) VALUES ('experiment-{name}',{PRICE},0,1); SELECT LAST_INSERT_ID();").strip())
            meta["productId"] = product_id
            for index, point in enumerate(points):
                point_name = f"{index+1:03d}-r{point['repeat']}-{point['workload']}-{point['rate']}-{point['path']}"
                point_dir = directory / point_name
                point_dir.mkdir()
                point["userPrefix"] = "x" + secrets.token_hex(6)
                meta["points"].append({"directory": point_name, "userPrefix": point["userPrefix"]})
                write_json(directory / "manifest.json", meta)
                current_metrics = require_idle(admin["token"])
                if (current_metrics["settings"] != metrics["settings"]
                        or current_metrics["localCacheEnabled"] != metrics["localCacheEnabled"]
                        or current_metrics.get("processStartedAtMs") != metrics.get("processStartedAtMs")):
                    raise RuntimeError("Backend settings/process changed between measurement points")
                print(f"测量 {point_name}: {point['scheduled']} 请求", flush=True)
                prepare(point, product_id, private / point_name, point_dir, admin["token"])
                meta["points"][-1]["activityId"] = point["activityId"]
                write_json(directory / "manifest.json", meta)
                value = measure(point, private / point_name, point_dir, admin["token"], args)
                results.append((point_name, value))
                report(directory, meta, results)
                # A dropped load point is invalid for capacity comparisons, but does not
                # justify discarding subsequent independently prepared, fully drained points.
                load_errors = {"Offered load not achieved (dropped/missing iterations)"}
                if value["k6ExitCode"] == 99:
                    load_errors.add("k6 exited with a non-zero status")
                fatal = [e for e in value["correctnessErrors"] if e not in load_errors]
                if fatal:
                    raise RuntimeError(f"Measurement invalid: {fatal}")
            meta["status"] = "completed"
            return 0
        except BaseException as error:
            meta["status"] = "failed"
            meta["error"] = str(error)
            raise
        finally:
            meta["finishedAtMs"] = int(time.time()*1000)
            report(directory, meta, results)
            write_json(directory / "manifest.json", meta)
            print(f"现场与报告已保留：{directory}", flush=True)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        sys.exit(main())
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f"实验终止：{error}", file=sys.stderr)
        sys.exit(1)
