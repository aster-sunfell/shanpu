#!/usr/bin/env python3
"""Keep experiment evidence in docs; never serialize credentials or tokens."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[3]


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    action, directory, *args = sys.argv[1:]
    report = Path(directory)
    manifest_file = report / "manifest.json"
    if action == "start":
        files = git("ls-files", "-co", "--exclude-standard").splitlines()
        sources = {}
        for name in files:
            path = ROOT / name
            if path.is_file() and (name.startswith(("backend/", "scripts/", "deploy/"))
                                   or name in ("docker-compose.yml", ".sdkmanrc")):
                sources[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        names = ("MYSQL_DATABASE", "MYSQL_PORT", "REDIS_PORT", "KAFKA_PORT", "BACKEND_PORT",
                 "PRESSURE_STOCK", "USER_RANGE_MAX", "BENCH_POINTS")
        memory = Path("/proc/meminfo")
        write_json(manifest_file, {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "running",
            "suite": "legacy-seckill",
            "git_commit": git("rev-parse", "HEAD"),
            "git_status": git("status", "--porcelain"),
            "source_sha256": sources,
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "memory": memory.read_text().splitlines()[0] if memory.exists() else None,
            "parameters": {name: os.environ[name] for name in names if name in os.environ},
            "pressure": {"vus": 100, "duration_seconds": 30},
            "limitations": "Legacy A/B/C/D suite; C responses count acceptance, not completion. See docs/experiments/PLAN.md.",
        })
    elif action == "config":
        config = json.load(sys.stdin)
        # Compose config contains passwords and Redis command-line credentials.
        # Retain only public topology/resource fields, never environment or commands.
        write_json(report / "resources.json", {
            name: {key: service[key] for key in ("image", "deploy", "ports", "network_mode") if key in service}
            for name, service in config["services"].items()
        })
    elif action == "finish":
        status = int(args[0])
        manifest = json.loads(manifest_file.read_text())
        manifest.update(finished_at=datetime.now(timezone.utc).isoformat(),
                        status="completed" if status == 0 else "failed", exit_code=status,
                        database_state="preserved")
        write_json(manifest_file, manifest)
        summary_file = report / "summary.md"
        summary = summary_file.read_text() if summary_file.exists() else "本轮未生成完整汇总，请检查原始输出。\n"
        (report / "report.md").write_text(
            "# 旧版秒杀及缓存实验\n\n"
            f"执行状态：{manifest['status']}，退出码：{status}。\n\n"
            "配置与代码指纹见 [manifest.json](manifest.json)，原始 k6 输出见本目录 `k6-*.out`。\n\n"
            "这是迁移保留的 A/B/C/D 场景，尚未实现新 A/B、B/C 实验设计。"
            "C 的 PENDING 响应表示受理，不可与同步完成吞吐直接比较。"
            "数据库现场保留，需人工核对后再显式清理。\n\n" + summary)
    else:
        raise SystemExit(f"unknown action: {action}")


if __name__ == "__main__":
    main()
