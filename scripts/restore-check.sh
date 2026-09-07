#!/usr/bin/env bash
# 电商经营数据平台 — 非破坏性备份恢复演练
# 只把最新 pg_dump 恢复到临时数据库，验证迁移可执行后自动删除临时库；绝不修改生产数据库。
# 用法：
#   ./scripts/restore-check.sh
#   BACKUP_DIR=/mnt/nas ./scripts/restore-check.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  source "$ROOT/.env"
  set +a
fi

for cmd in pg_restore createdb dropdb psql; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "缺少命令：$cmd"
    exit 2
  }
done

LATEST_DUMP="$(ls -1t "$BACKUP_DIR"/db_*.dump 2>/dev/null | head -1 || true)"
if [[ -z "$LATEST_DUMP" ]]; then
  echo "未找到数据库备份：$BACKUP_DIR/db_*.dump"
  exit 2
fi

HOST="${POSTGRES_HOST:-localhost}"
PORT="${POSTGRES_PORT:-5432}"
USER="${POSTGRES_USER:-ecommerce}"
PROD_DB="${POSTGRES_DB:-ecommerce}"
STAMP="$(date +%Y%m%d_%H%M%S)_$$"
CHECK_DB="${PROD_DB}_restorecheck_${STAMP}"
export PGPASSWORD="${POSTGRES_PASSWORD:-}"

cleanup() {
  dropdb -h "$HOST" -p "$PORT" -U "$USER" --if-exists "$CHECK_DB" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

echo "==> 使用备份：$LATEST_DUMP"
echo "==> 创建临时数据库：$CHECK_DB"
createdb -h "$HOST" -p "$PORT" -U "$USER" "$CHECK_DB"

# --exit-on-error：任意对象/数据恢复失败立即判定备份不可用。
pg_restore \
  -h "$HOST" -p "$PORT" -U "$USER" -d "$CHECK_DB" \
  --no-owner --no-privileges --exit-on-error \
  "$LATEST_DUMP"

TABLE_COUNT="$(psql -h "$HOST" -p "$PORT" -U "$USER" -d "$CHECK_DB" -Atc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")"
if [[ -z "$TABLE_COUNT" || "$TABLE_COUNT" -le 0 ]]; then
  echo "恢复失败：临时数据库没有业务表"
  exit 1
fi

echo "==> 恢复成功，public 表数量：$TABLE_COUNT"

# 用当前代码对恢复库执行 Alembic upgrade head，验证老备份可以迁移到当前版本。
ALEMBIC="$ROOT/backend/.venv/bin/alembic"
if [[ -x "$ALEMBIC" ]]; then
  export DATABASE_URL="postgresql+psycopg://${USER}:${POSTGRES_PASSWORD:-}@${HOST}:${PORT}/${CHECK_DB}"
  (
    cd "$ROOT/backend"
    "$ALEMBIC" upgrade head
    "$ALEMBIC" current
  )
  echo "==> 当前代码迁移兼容性：通过"
else
  echo "==> 未发现 backend/.venv/bin/alembic，仅完成数据库恢复验证，跳过迁移兼容检查"
fi

LATEST_DATA="$(ls -1t "$BACKUP_DIR"/data_*.tar.gz 2>/dev/null | head -1 || true)"
if [[ -n "$LATEST_DATA" ]]; then
  tar -tzf "$LATEST_DATA" >/dev/null
  echo "==> data 归档完整性：通过 ($(basename "$LATEST_DATA"))"
fi

echo "==> 恢复演练通过；临时数据库将在退出时自动删除"
