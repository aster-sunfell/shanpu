FROM grafana/k6:0.50.0 AS k6
FROM python:3.11-slim
COPY --from=k6 /usr/bin/k6 /usr/local/bin/k6
COPY experiments/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
WORKDIR /workspace
COPY scripts/experiments /workspace/scripts/experiments
COPY experiments/compose.sut.yml /workspace/experiments/compose.sut.yml
ENV GOMAXPROCS=2 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 BENCH_CONTAINER=1
ENTRYPOINT ["python3", "/workspace/scripts/experiments/lib/runner.py"]
