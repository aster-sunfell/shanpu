#!/usr/bin/env bash

# shellcheck source=dev-common.sh
. "$(dirname "${BASH_SOURCE[0]}")/dev-common.sh"

mysql_exec() {
  compose exec -T -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql \
    mysql -uroot --default-character-set=utf8mb4 "$MYSQL_DATABASE" "$@"
}
