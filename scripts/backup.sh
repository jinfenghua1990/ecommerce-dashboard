#!/usr/bin/env bash
# 电商经营数据平台 — 备份脚本
# 备份对象：PostgreSQL（pg_dump 逻辑备份）+ /data 归档文件（tar.gz）。
# 原生运行优先；保留 Docker 运行方式的兼容分支。
# 用法：
#   ./scripts/backup.sh                 # 备份到 ./backups/
#   BACKUP_DIR=/mnt/nas ./scripts/backup.sh
#   ./scripts/backup.sh --no-files      # 只备份数据库，跳过 /data
#
# 建议配合 cron（macOS launchd / Linux crontab）每日 02:30 跑一次。
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

# 1) PostgreSQL 逻辑备份（含 schema + data）
echo "==> [1/2] pg_dump ..."
TMP_DUMP="$BACKUP_DIR/.db_$TS.dump.tmp"
trap 'rm -f "$TMP_DUMP"' EXIT
if command -v docker >/dev/null 2>&1 \
  && docker compose -f "$ROOT/docker-compose.yml" ps --status running postgres 2>/dev/null | grep -q postgres; then
  docker compose -f "$ROOT/docker-compose.yml" exec -T postgres \
    pg_dump -U "${POSTGRES_USER:-ecommerce}" -Fc "${POSTGRES_DB:-ecommerce}" \
    > "$TMP_DUMP"
else
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
