# 电商经营数据平台 — 原生 macOS 运维入口
# 用法: make <target>

ROOT := $(CURDIR)
BACKEND := $(ROOT)/backend
VENV := $(BACKEND)/.venv
LAUNCH_LABEL := gui/$(shell id -u)/com.gino.ecommerce-dashboard
NATIVE_ENV = set -a; . "$(ROOT)/.env"; set +a; export DATABASE_URL="postgresql+psycopg://$$POSTGRES_USER:$$POSTGRES_PASSWORD@localhost:5432/$$POSTGRES_DB"; export REDIS_URL="redis://localhost:6379/0"; export DATA_DIR="$(ROOT)/data";

.PHONY: help up restart status logs logs-api rebuild rebuild-fe test lint tsc verify secret-scan smoke migrate migration-check exec-api backup restore-check orphan-audit backup-schedule-install backup-schedule-status backup-schedule-uninstall fresh

help: ## 列出所有 target
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*?##/ {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

up: restart ## 启动或重新加载本地服务

restart: ## 重新加载 API、worker 与 beat
	launchctl kickstart -k "$(LAUNCH_LABEL)"

status: ## 显示本机健康状态
	curl --noproxy '*' -fsS http://127.0.0.1:8000/healthz; echo
	launchctl print "$(LAUNCH_LABEL)" | sed -n '1,45p'

logs: ## 跟踪 API、worker、beat 日志
	tail -n 100 -f /tmp/ecom_api.log /tmp/ecom_worker.log /tmp/ecom_beat.log

logs-api: ## 跟踪 API 日志
	tail -n 100 -f /tmp/ecom_api.log

rebuild: restart ## 后端代码已直接由原生虚拟环境加载，重启即可

rebuild-fe: ## 重建静态前端并重启服务
	cd frontend && npm run build
	$(MAKE) restart

test: ## 在本机虚拟环境运行后端测试
	$(NATIVE_ENV) cd "$(BACKEND)" && "$(VENV)/bin/python" -m pytest app/tests -q --tb=line -p no:cacheprovider

lint: ## 编译检查后端 Python 文件
	cd "$(BACKEND)" && "$(VENV)/bin/python" -m compileall -q app

tsc: ## 前端类型检查
	cd frontend && npx tsc --noEmit

secret-scan: ## 扫描 Git 已跟踪文件中的高置信度 token / 私钥
	bash ./scripts/secret-scan.sh

verify: secret-scan migration-check orphan-audit lint test tsc ## 本地一键验收：敏感信息 + 真实库引用 + 迁移 + 后端 + 前端静态构建
	cd frontend && npm run build
	@test -f frontend/out/index.html
	@echo "本地验收通过：secret scan / migration / orphan audit / backend tests / TypeScript / static build 均正常。"

smoke: ## 枚举公开 API 并做带鉴权 smoke test
	./scripts/smoke.sh

migrate: ## 应用 Alembic 迁移
	$(NATIVE_ENV) cd "$(BACKEND)" && "$(VENV)/bin/alembic" upgrade head

migration-check: ## 检查 models 与迁移是否漂移
	$(NATIVE_ENV) cd "$(BACKEND)" && "$(VENV)/bin/alembic" check

exec-api: ## 进入后端原生虚拟环境 shell
	$(NATIVE_ENV) cd "$(BACKEND)" && exec "$(SHELL)"

backup: ## 备份 PostgreSQL 与 data/ 原始归档
	./scripts/backup.sh

restore-check: ## 将最新备份恢复到临时库验证，生产库不做任何修改
	bash ./scripts/restore-check.sh

orphan-audit: ## 只读检查关键业务表孤儿引用，补 FK/约束前必须为 0
	bash ./scripts/orphan-audit.sh

backup-schedule-install: ## 安装 macOS 每日备份 + 每周恢复演练 launchd 计划
	bash ./scripts/backup-schedule.sh install

backup-schedule-status: ## 查看 macOS 自动备份计划状态
	bash ./scripts/backup-schedule.sh status

backup-schedule-uninstall: ## 卸载 macOS 自动备份计划
	bash ./scripts/backup-schedule.sh uninstall

fresh: ## 拒绝自动清空真实业务数据
	@echo "拒绝执行：fresh 会销毁真实数据；如确有需要，请先单独确认目标与备份。"
	@exit 2
