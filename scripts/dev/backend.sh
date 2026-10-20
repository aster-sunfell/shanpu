#!/usr/bin/env bash
set -euo pipefail

. "$(dirname "${BASH_SOURCE[0]}")/../lib/dev-common.sh"
load_env
export MYSQL_DATABASE=${EXPERIMENT_DATABASE:-$MYSQL_DATABASE}

cd "$ROOT_DIR"
java_version=$(sed -n 's/^java=//p' .sdkmanrc)
sdkman_init="${SDKMAN_DIR:-$HOME/.sdkman}/bin/sdkman-init.sh"
if [ ! -s "$sdkman_init" ]; then
  printf '请先安装 SDKMAN 和项目所需的 Java %s。\n' "$java_version" >&2
  exit 1
fi
# SDKMAN 的初始化脚本会访问尚未定义的变量。
set +u
if ! . "$sdkman_init" || ! sdk env; then
  set -u
  printf 'SDK 环境初始化失败；请通过 SDKMAN 安装 .sdkmanrc 中指定的版本。\n' >&2
  exit 1
fi
set -u
sdk_java="$SDKMAN_CANDIDATES_DIR/java/$java_version"
if [ "$(readlink -f "$JAVA_HOME")" != "$(readlink -f "$sdk_java")" ] \
    || [ "$(readlink -f "$(command -v java)")" != "$(readlink -f "$sdk_java/bin/java")" ]; then
  printf '当前 Java 与项目 SDK 不一致，要求 %s，已停止启动。\n' "$java_version" >&2
  exit 1
fi
printf '项目 SDK：%s\n' "$JAVA_HOME"
java -version

export TZ=Asia/Shanghai
export SPRING_PROFILES_ACTIVE=${SPRING_PROFILES_ACTIVE:-dev}
export SHANPU_EXPERIMENT_JOURNAL=${SHANPU_EXPERIMENT_JOURNAL:-$ROOT_DIR/.local/experiments/commits.jsonl}
export SERVER_ADDRESS=127.0.0.1
export SERVER_PORT="$BACKEND_PORT"
export MYSQL_HOST=127.0.0.1 MYSQL_USERNAME=root MYSQL_PASSWORD="$MYSQL_ROOT_PASSWORD"
export REDIS_HOST=127.0.0.1
export KAFKA_BOOTSTRAP_SERVERS="127.0.0.1:$KAFKA_PORT"

cd "$ROOT_DIR/backend"
mvn -B -ntp -DskipTests clean package

mkdir -p "$DEV_RUNTIME_DIR"
printf '%s\n' "$$" > "$DEV_RUNTIME_DIR/backend.pid"
read -r -a java_opts <<< "${JAVA_OPTS:--Xms256m -Xmx1g}"
exec java "${java_opts[@]}" -Duser.timezone=Asia/Shanghai -jar target/shanpu-backend-1.0.0-exec.jar
