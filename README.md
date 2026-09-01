# 电商经营数据平台 V1

连接 **吉客云（唯一商品/SKU 主档）+ 1688（采购交易来源）+ 浙江农信（原始资金资料）** 的经营数据平台。

## 技术栈

- 后端：FastAPI + SQLAlchemy 2 + Alembic + Pydantic（Python 3.12）
- 前端：Next.js 15 + React 19 + Tailwind CSS 4
- 任务：Celery Worker + Celery Beat（Redis broker）
- 基础：PostgreSQL 17 / Redis 7 / Docker Compose / 本地 `/data` 归档
- 金额：全链路 Decimal / Numeric(18,4)，禁止 float

## 启动

```bash
cp .env.example .env   # 填入真实凭证
docker compose up -d --build
```

- Web：http://localhost:18080 （局域网 http://<本机IP>:18080）
- API：容器内 8000，经前端 `/api/*` 反代；本机调试端口 127.0.0.1:25432(Postgres) 26379(Redis)

访问模式：`ACCESS_MODE=lan_trusted`（局域网信任，无登录）。**切勿端口转发到公网**；如需公网必须先恢复认证 + TLS。

## 首次上线清单

```text
[ ] 重置此前暴露过的吉客云 MCP Token（旧 Token 一律作废）
[ ] 填写新的吉客云 Token 到 .env
[ ] 设置页「立即测试连接」验证吉客云 MCP（initialize → tools/list）
[ ] 同步商品/SKU（Phase 1 mapping 完成后）
[ ] 同步订单
[ ] 配置公司主体
[ ] 完成期初初始化（允许不平，差异进差异池）
[ ] 配置 SMTP 和财务邮箱
[ ] 创建 1688 开放平台应用，配置 OAuth 回调
[ ] 测试 1688 订单同步（每天 1 次 + 手动立即同步）
[ ] 上传浙江农信测试 Excel/PDF（SHA256 + 版本化，同名不覆盖）
[ ] 生成测试财务 ZIP
[ ] 测试邮件发送（人工确认后）
[ ] 做数据库和 /data 备份
```

## 目录结构

```text
backend/    FastAPI 应用（app/models 45+ 表、app/adapters 四个 Adapter、app/api/v1、Celery）
frontend/   Next.js 前端（10 项菜单，未落地的 Phase 明确占位不造假数据）
data/       原始文件长期归档 /data（财务资料按 公司/年/月/original 分类）
docs/       实施状态、ER 图、吉客云字段 mapping、外部集成说明
```

## 业务原则（必须遵守）

1. 吉客云是唯一商品/SKU 主档，关联主键用吉客云内部 ID / SKU 编码
2. 1688 只读同步已发生的买家订单，**不下单、不付款**；"定制专拍"标题不得建成 SKU
3. 浙江农信只做文件导入，不做银企直联、不做网页 RPA
4. 给财务的是原始资料：原样 ZIP、已发送版本不可覆盖，修正生成 V2/V3
5. 期初允许不平，历史差异进差异池，所有调整写审计日志
6. 未配置的外部系统如实显示"未配置"，禁止用模拟数据伪装连接

## 数据库迁移

```bash
docker compose exec api alembic upgrade head          # 应用迁移
docker compose exec api alembic revision --autogenerate -m "..."  # 生成新迁移
```

## 备份 / 恢复

```bash
docker compose exec postgres pg_dump -U ecommerce ecommerce > backup_$(date +%F).sql
docker compose exec -T postgres psql -U ecommerce ecommerce < backup_xxxx.sql
tar czf data_backup_$(date +%F).tgz data/    # /data 原始文件归档
```

## 升级说明

```bash
# 1. 拉取最新代码，先备份（见上节）
git pull
# 2. 重新构建并滚动重启（数据卷不动，pgdata/redisdata/data 保留）
docker compose build
docker compose up -d
# 3. 应用新迁移（如有）
docker compose exec api alembic upgrade head
# 4. 验证
curl -s http://127.0.0.1:18080/healthz
docker compose ps
```

升级注意事项：

- **数据库结构变更一律走 Alembic 迁移**，禁止手改表结构；`alembic upgrade head` 幂等可重复执行
- **已发送给财务的 V1/V2 包不可覆盖**：升级不会触碰 `data/finance/*/output/` 已生成 ZIP（文件名带版本号天然隔离）
- **原始文件只读归档**：升级不影响 `data/finance/*/original/`，同名上传自动 version 递增
- **Celery 任务**：升级后 worker/beat 随 compose 重启自动加载新代码；未配置的外部同步（吉客云/1688/SMTP）如实跳过并写日志
- **回滚**：代码回滚用 `git checkout <上一commit> && docker compose build && docker compose up -d`；数据回滚用备份 SQL 恢复（注意备份时间点之后的写入会丢失，先确认）

