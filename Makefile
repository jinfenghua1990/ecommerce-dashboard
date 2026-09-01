# 电商经营数据平台 — 本地运维入口
# 用法: make <target>

COMPOSE := docker compose
APP    := api
WORKER := worker
BEAT   := beat

.PHONY: help
help: ## 列出所有 target
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*?##/ {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: up
up: ## 启动全部 6 服务（首次会 build）
	$(COMPOSE) up -d --build

.PHONY: down
down: ## 停止并清理
	$(COMPOSE) down

.PHONY: rebuild
rebuild: ## 重新 build api/worker/beat 并重启
	$(COMPOSE) build $(APP) $(WORKER) $(BEAT)
	$(COMPOSE) up -d --no-deps $(APP) $(WORKER) $(BEAT)

.PHONY: rebuild-fe
rebuild-fe: ## 重新 build 前端并重启（改 frontend/ 后）
	$(COMPOSE) build frontend
	$(COMPOSE) up -d --no-deps frontend

.PHONY: restart
restart: ## 不重建仅重启 3 容器
	$(COMPOSE) up -d --no-deps $(APP) $(WORKER) $(BEAT)

.PHONY: logs
logs: ## tail 所有日志
	$(COMPOSE) logs -f --tail=100

.PHONY: logs-api
logs-api: ## tail api 日志
	$(COMPOSE) logs -f --tail=100 $(APP)

.PHONY: test
test: ## 在 api 容器内跑 pytest
	$(COMPOSE) run --rm $(APP) python -m pytest app/tests -q --tb=line

.PHONY: lint
lint: ## pyflakes 检查
	cd backend && python3 -m pyflakes app

.PHONY: tsc
tsc: ## 前端类型检查
	cd frontend && npx tsc --noEmit

.PHONY: smoke
smoke: ## GET 端点 0-5xx smoke test（基于 OpenAPI 路由全集）
	./scripts/smoke.sh

.PHONY: migrate
migrate: ## alembic upgrade head
	$(COMPOSE) run --rm $(APP) alembic upgrade head

.PHONY: migration-check
migration-check: ## 对比 models 与最新迁移是否漂移
	$(COMPOSE) run --rm $(APP) alembic check

.PHONY: exec-api
exec-api: ## 进入 api 容器 shell
	$(COMPOSE) exec $(APP) bash

.PHONY: fresh
fresh: ## ⚠️ 销毁所有数据并重建（删 PG volume）
	$(COMPOSE) down -v
	$(COMPOSE) up -d --build
