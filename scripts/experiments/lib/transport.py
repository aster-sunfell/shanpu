"""Direct protocol clients on the load host; never mount the Docker socket."""
import os
import threading

_kafka = None
_kafka_lock = threading.Lock()


def host():
    return os.environ.get("SUT_HOST", "127.0.0.1")


def sql(statement):
    import pymysql
    from pymysql.constants import CLIENT
    connection = pymysql.connect(host=host(), port=13306, user="root", password=os.environ["MYSQL_ROOT_PASSWORD"],
                                 database=os.environ["MYSQL_DATABASE"], charset="utf8mb4", autocommit=True,
                                 connect_timeout=5, read_timeout=30, write_timeout=30,
                                 client_flag=CLIENT.MULTI_STATEMENTS)
    rows = []
    try:
        with connection.cursor() as cursor:
            cursor.execute(statement)
            while True:
                if cursor.description:
                    rows.extend("\t".join("NULL" if value is None else str(value) for value in row) for row in cursor)
                if not cursor.nextset(): break
    finally:
        connection.close()
    return "\n".join(rows)



def sql_rows(statement):
    """Unbuffered query, with one server row in Python at a time."""
    import pymysql
    connection = pymysql.connect(host=host(), port=13306, user="root", password=os.environ["MYSQL_ROOT_PASSWORD"],
                                 database=os.environ["MYSQL_DATABASE"], charset="utf8mb4", autocommit=True,
                                 connect_timeout=5, read_timeout=30, write_timeout=30,
                                 cursorclass=pymysql.cursors.SSCursor)
    try:
        with connection.cursor() as cursor:
            cursor.execute(statement)
            yield from cursor
    finally:
        connection.close()


def redis_members(key):
    import redis
    client = redis.Redis(host=host(), port=16379, password=os.environ["REDIS_PASSWORD"], decode_responses=True,
                         socket_connect_timeout=5, socket_timeout=10)
    try:
        yield from client.sscan_iter(key, count=500)
    finally:
        client.close()

def redis_command(*args):
    import redis
    client = redis.Redis(host=host(), port=16379, password=os.environ["REDIS_PASSWORD"], decode_responses=True,
                         socket_connect_timeout=5, socket_timeout=10)
    try:
        result = client.execute_command(*args)
        if result is None: return ""
        if isinstance(result, (set, list, tuple)): return "\n".join(map(str, result))
        if isinstance(result, bool): return "1" if result else "0"
        return str(result)
    finally:
        client.close()


def sessions(users):
    import hashlib
    import redis
    client = redis.Redis(host=host(), port=16379, password=os.environ["REDIS_PASSWORD"],
                         socket_connect_timeout=5, socket_timeout=10)
    try:
        with client.pipeline(transaction=False) as pipe:
            for index, user in enumerate(users):
                digest = hashlib.sha256(user["token"].encode()).hexdigest()[:32]
                pipe.setex(f"shanpu:user:session:{digest}", 86400, user["userId"])
                if index % 500 == 499: pipe.execute()
            pipe.execute()
    finally:
        client.close()


def lag():
    with _kafka_lock:
        return _lag()


def _lag():
    global _kafka
    from kafka import KafkaConsumer, TopicPartition
    if _kafka is None:
        _kafka = KafkaConsumer(bootstrap_servers=[f"{host()}:19092"], group_id="shanpu-seckill-consumer",
                               enable_auto_commit=False, api_version=(2, 6, 0), request_timeout_ms=10000, session_timeout_ms=6000,
                               api_version_auto_timeout_ms=5000, max_partition_fetch_bytes=262144)
    partitions = sorted(_kafka.partitions_for_topic("shanpu.seckill.order") or [])
    if partitions != [0, 1, 2]: raise RuntimeError("Cannot confirm all three Kafka partitions")
    topics = [TopicPartition("shanpu.seckill.order", p) for p in partitions]
    ends = _kafka.end_offsets(topics)
    result = {}
    for topic in topics:
        current = _kafka.committed(topic)
        if current is None:
            if ends[topic] != 0: raise RuntimeError("Uncommitted Kafka partition has messages")
            current = 0
        result[str(topic.partition)] = ends[topic] - current
    return result
