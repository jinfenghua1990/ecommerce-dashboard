#!/usr/bin/env bash
# 电商经营数据平台 — 备份脚本
# 备份对象：PostgreSQL（pg_dump 逻辑备份）+ /data 归档文件（tar.gz）。
# 原生运行优先；保留 Docker 运行方式的兼容分支。
# 用法：
#   ./scripts/backup.sh                 # 备份到 ./backups/
#   BACKUP_DIR=/mnt/nas ./scripts/backup.sh
#   ./scripts/backup.sh --no-files      # 只备份数据库，跳过 /data
#
# 建议配合 macOS launchd 每日 02:30 跑一次。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
KEEP="${KEEP:-14}"          # 保留最近 N 份
TS="$(date +%Y%m%d_%H%M%S)"
SKIP_FILES=0
[[ "${1:-}" == "--no-files" ]] && SKIP_FILES=1

if [[ -f "$ROOT/.env" ]]; then
  set -a
  source "$ROOT/.env"
  set +a
fi

mkdir -p "$BACKUP_DIR"

echo "==> 备份到 $BACKUP_DIR (保留 $KEEP 份) @ $TS"

check_local_pg_client() {
  command -v pg_dump >/dev/null 2>&1 || {
    echo "缺少 pg_dump，无法执行数据库备份。"
    exit 2
  }
  command -v psql >/dev/null 2>&1 || {
    echo "缺少 psql，无法核对 PostgreSQL 客户端/服务器版本。"
    exit 2
  }

  local host="${POSTGRES_HOST:-localhost}"
  local port="${POSTGRES_PORT:-5432}"
  local user="${POSTGRES_USER:-ecommerce}"
  local db="${POSTGRES_DB:-ecommerce}"
  local server_num client_major server_major

  server_num="$(PGPASSWORD="${POSTGRES_PASSWORD:-}" psql \
    -h "$host" -p "$port" -U "$user" -d "$db" -Atc "SHOW server_version_num" 2>/dev/null || true)"
  client_major="$(pg_dump --version | awk '{print $3}' | cut -d. -f1)"

  if [[ "$server_num" =~ ^[0-9]+$ && "$client_major" =~ ^[0-9]+$ ]]; then
    server_major=$((server_num / 10000))
    if (( client_major < server_major )); then
      echo "PostgreSQL 客户端版本过旧：服务器=$server_major，pg_dump=$client_major。"
      echo "请先安装/切换到 PostgreSQL $server_major 对应客户端，再执行备份。"
      echo "为避免生成不可用备份，本次操作已在 pg_dump 前终止。"
      exit 2
    fi
  fi
}

# 1) PostgreSQL 逻辑备份（含 schema + data）
echo "==> [1/2] pg_dump ..."
TMP_DUMP="$BACKUP_DIR/.db_$TS.dump.tmp"
trap 'rm -f "$TMP_DUMP"' EXIT
if command -v docker >/dev/null 2>&1 \
  && docker compose -f "$ROOT/docker-compose.yml" ps --status running postgres 2>/dev/null | grep -q postgres; then
  # Compose 模式直接使用 PostgreSQL 容器自身的 pg_dump，版本天然与服务端一致。
  docker compose -f "$ROOT/docker-compose.yml" exec -T postgres \
    pg_dump -U "${POSTGRES_USER:-ecommerce}" -Fc "${POSTGRES_DB:-ecommerce}" \
    > "$TMP_DUMP"
else
  check_local_pg_client
  PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_dump \
    -h "${POSTGRES_HOST:-localhost}" -p "${POSTGRES_PORT:-5432}" \
    -U "${POSTGRES_USER:-ecommerce}" -Fc "${POSTGRES_DB:-ecommerce}" \
    > "$TMP_DUMP"
fi
mv "$TMP_DUMP" "$BACKUP_DIR/db_$TS.dump"
trap - EXIT

# 2) /data 归档文件（可选）
if [[ "$SKIP_FILES" == "0" ]]; then
  echo "==> [2/2] 归档 /data ..."
  if [[ -d "$ROOT/data" ]]; then
    tar -czf "$BACKUP_DIR/data_$TS.tar.gz" -C "$ROOT" data
  else
    echo "     (无 $ROOT/data，跳过)"
  fi
fi

# 3) 滚动清理，保留最近 KEEP 份
for prefix in db_ data_; do
  count=0
  while IFS= read -r old_file; do
    count=$((count + 1))
    if (( count > KEEP )); then
      rm -f "$old_file"
    fi
  done < <(ls -1t "$BACKUP_DIR"/${prefix}* 2>/dev/null || true)
done

echo "==> 完成。当前备份："
ls -1t "$BACKUP_DIR" | head -20
