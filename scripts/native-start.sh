#!/bin/bash
# ecommerce-dashboard 原生启动脚本（由 LaunchAgent com.gino.ecommerce-dashboard 调用）
# 依赖：原生 Postgres16(brew) + Redis(brew) 已在跑；backend/.venv 已建；前端已 build
set -a
source /Users/gino/ecommerce-dashboard/.env
set +a

export DATABASE_URL="postgresql+psycopg://$POSTGRES_USER:$POSTGRES_PASSWORD@localhost:5432/$POSTGRES_DB"
export REDIS_URL="redis://localhost:6379/0"
export DATA_DIR=/Users/gino/ecommerce-dashboard/data

VENV=/Users/gino/ecommerce-dashboard/backend/.venv
export PATH="/Users/gino/.workbuddy/binaries/node/versions/22.22.2-2/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# ---------- 后端：api / worker / beat ----------
source "$VENV/bin/activate"
cd /Users/gino/ecommerce-dashboard/backend
python -m app.seed > /tmp/ecom_seed.log 2>&1
uvicorn app.main:app --host 0.0.0.0 --port 8000 >> /tmp/ecom_api.log 2>&1 &
celery -A app.celery_app worker -l info --concurrency 2 >> /tmp/ecom_worker.log 2>&1 &
celery -A app.celery_app beat -l info --schedule /tmp/celerybeat-schedule >> /tmp/ecom_beat.log 2>&1 &
deactivate

# ---------- 前端（静态导出，由后端 8000 同口托管，不再单独跑 next start） ----------
cd /Users/gino/ecommerce-dashboard/frontend
# 缺少导出产物时自动构建（正常重启已有 out/ 会跳过）
if [ ! -f out/index.html ]; then
  echo "[native-start] 未发现 frontend/out，执行 next export 构建..."
  npm run build >> /tmp/ecom_fe_build.log 2>&1
fi

# 保持脚本存活，使 launchd 的 KeepAlive 能正确监控
wait
