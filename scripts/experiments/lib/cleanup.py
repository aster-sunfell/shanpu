"""Explicitly delete only verified, completed experiment fixtures. Reports remain intact."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from analysis import write_json
from runner import ROOT, RUNTIME, require_idle, redis, sql


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report_directory", type=Path)
    args = parser.parse_args()
    directory = args.report_directory.resolve()
    relative = directory.relative_to(ROOT / "docs/experiments")
    if len(relative.parts) != 2 or relative.parts[0] not in ("redis-filtering", "kafka-buffering"):
        raise RuntimeError("Not an experiment report directory")
    meta = json.loads((directory / "manifest.json").read_text())
    recorded_database = meta.get("database") or json.loads((directory / "backend-settings.json").read_text())["settings"]["database"]
    if recorded_database != os.environ["MYSQL_DATABASE"]:
        raise RuntimeError("Report belongs to another database")
    if meta.get("dataState") == "cleaned":
        raise RuntimeError("This experiment was already cleaned")
    # Failed runs require investigation; cleanup never uses lag alone to discard their evidence.
    if meta["status"] != "completed":
        raise RuntimeError("Failed/incomplete experiment: preserve evidence and reconcile manually")
    with (RUNTIME / "experiment.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        private = RUNTIME / directory.name
        admin = json.loads((private / "admin.json").read_text())[0]
        require_idle(admin["token"])
        product_id = int(meta["productId"])
        title = sql(f"SELECT title FROM t_product WHERE id={product_id};").strip()
        if title != f"experiment-{directory.name}":
            raise RuntimeError("Product ownership mismatch")
        activity_ids, prefixes, credentials, order_keys = [], [], [private / "admin.json"], []
        for point in meta["points"]:
            if Path(point["directory"]).name != point["directory"]:
                raise RuntimeError("Invalid measurement directory")
            prefix, aid = point["userPrefix"], int(point["activityId"])
            if not re.fullmatch(r"x[0-9a-f]{12}", prefix):
                raise RuntimeError("Invalid fixture prefix")
            result = json.loads((directory / point["directory"] / "result.json").read_text())
            if result["correctnessErrors"] or result["unresolved"]:
                raise RuntimeError("Unresolved results must not be deleted")
            if sql(f"SELECT title FROM t_seckill_activity WHERE id={aid} AND product_id={product_id};").strip() != prefix:
                raise RuntimeError("Activity ownership mismatch")
            others = sql(f"SELECT COUNT(*) FROM t_order o JOIN t_user u ON u.id=o.user_id WHERE "
                         f"(o.activity_id={aid} AND u.username NOT REGEXP '^{prefix}_[0-9]+$') OR "
                         f"(u.username REGEXP '^{prefix}_[0-9]+$' AND (o.activity_id IS NULL OR o.activity_id<>{aid}));").strip()
            if others != "0":
                raise RuntimeError("Foreign orders reference this fixture")
            for row in sql(f"SELECT user_id,order_id FROM t_order WHERE activity_id={aid};").splitlines():
                uid, oid = row.split("\t")
                order_keys.append(f"shanpu:order:detail:user:{uid}:{oid}")
            prefixes.append(prefix)
            activity_ids.append(aid)
            credentials.append(private / point["directory"] / "users.json")
        prefixes.append(meta["adminPrefix"])
        if any(not re.fullmatch(r"x[0-9a-f]{12}", prefix) for prefix in prefixes):
            raise RuntimeError("Invalid fixture prefix")
        # Validate admin ownership too, then perform a single transaction after every check passes.
        if sql(f"SELECT COUNT(*) FROM t_order o JOIN t_user u ON u.id=o.user_id "
               f"WHERE u.username REGEXP '^{meta['adminPrefix']}_[0-9]+$';").strip() != "0":
            raise RuntimeError("Experiment admin unexpectedly has orders")
        ids = ",".join(map(str, activity_ids))
        user_filter = " OR ".join(f"u.username REGEXP '^{p}_[0-9]+$'" for p in prefixes)
        if sql(f"SELECT COUNT(*) FROM t_seckill_activity WHERE product_id={product_id} AND id NOT IN ({ids});").strip() != "0":
            raise RuntimeError("Product has an unrecognized activity")
        if sql(f"SELECT COUNT(*) FROM t_order WHERE product_id={product_id} AND "
               f"(activity_id IS NULL OR activity_id NOT IN ({ids}));").strip() != "0":
            raise RuntimeError("Product is referenced by foreign orders")
        sql(f"START TRANSACTION; DELETE l FROM t_point_log l JOIN t_user u ON u.id=l.user_id WHERE {user_filter}; "
            f"DELETE FROM t_order WHERE activity_id IN ({ids}); DELETE FROM t_seckill_activity WHERE id IN ({ids}); "
            f"DELETE u FROM t_user u WHERE {user_filter}; DELETE FROM t_product WHERE id={product_id}; COMMIT;")
        keys = order_keys
        for aid in activity_ids:
            keys.extend(f"shanpu:seckill:{part}:{aid}" for part in ("stock", "bought", "activity", "activity:lock", "activity:null"))
        for path in credentials:
            for user in json.loads(path.read_text()):
                digest = hashlib.sha256(user["token"].encode()).hexdigest()[:32]
                keys.append(f"shanpu:user:session:{digest}")
        for start in range(0, len(keys), 1000):
            redis("DEL", *keys[start:start+1000])
        # Credentials are private ephemeral artifacts; only explicit cleanup removes them.
        for path in credentials:
            path.unlink()
        meta["dataState"] = "cleaned"
        write_json(directory / "manifest.json", meta)
        print(f"已清理本轮独立实验数据，报告保留：{directory}")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError) as error:
        print(f"拒绝清理：{error}", file=sys.stderr)
        sys.exit(1)
