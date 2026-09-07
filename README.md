# 电商经营数据平台 V1

连接 **吉客云（唯一商品/SKU 主档）+ 1688（采购交易来源）+ 浙江农信（原始资金资料）** 的经营数据平台。

## 技术栈

- 后端：FastAPI + SQLAlchemy 2 + Alembic + Pydantic（Python 3.12）
- 前端：Next.js 16 + React 19 + Tailwind CSS 4
- 任务：Celery Worker + Celery Beat（Redis broker）
- 基础：本机 PostgreSQL / Redis / LaunchAgent / 本地 `data/` 归档
- 金额：全链路 Decimal / Numeric(18,4)，禁止 float

## 启动

```bash
cp .env.example .env   # 填入真实凭证
make up
```

- Web 与 API：http://localhost:8000 （局域网 http://<本机IP>:8000）
- PostgreSQL / Redis 只在本机 `5432` / `6379` 提供服务；前端静态产物由同一 FastAPI 端口托管。

当前固定使用 `ACCESS_MODE=rbac`：除登录、1688 OAuth 回调和 `/healthz` 外，全部 `/api/v1` 都需要登录令牌。`viewer` 只读，`operator/admin` 可执行写操作，用户管理仅限 `admin`。

登录页默认勾选“记住登录”：同一浏览器 30 天内免重复输入；取消勾选则令牌有效 12 小时。改密码或点击退出会让该账号此前签发的令牌立即失效。局域网只开放 Web 端口 8000，PostgreSQL/Redis 仅绑定 `127.0.0.1`。**不要把 8000 做公网端口转发**；公网访问必须另加 TLS 与网络访问控制。

服务由 LaunchAgent `com.gino.ecommerce-dashboard` 启动；入口脚本为 `scripts/native-start.sh`。后端、worker、beat 均使用 `backend/.venv` 与本机 PostgreSQL/Redis。

首次登录用 `.env` 里的 `ADMIN_USERNAME` / `ADMIN_PASSWORD`，登录后请立即在设置页改密码。

> `make smoke` 默认在本机为现有管理员签发 5 分钟诊断令牌，不读取或输出密码；也可用 `SMOKE_USER=xxx SMOKE_PASS=yyy make smoke` 显式验证真实登录链路。任一 401/403、非预期 4xx 或 5xx 都会使 smoke 失败；唯一允许的配置型 400 是未提供 1688 AppKey/Secret 时的明确阻塞提示。

## 本地开发（前后端同时改）

后端以原生虚拟环境运行，前端为静态导出。改动后按改动面重新加载：

```bash
make rebuild                                        # 改了 backend/ → 重启 api+worker+beat
make rebuild-fe                                     # 改了 frontend/ → 重新构建静态产物并重启
```

约定：

- 只改后端逻辑：`make rebuild`；迁移文件直接写到 `backend/alembic/versions/`
- 只改前端：`make rebuild-fe`，无需动后端
- 改依赖（requirements.txt / package.json）：更新虚拟环境或 `npm install` 后执行对应重建
- 前端类型检查：`make tsc`；后端 lint：`make lint`
- 回归一把梭：`make test && make lint && make tsc && make smoke && make migration-check`

## 首次上线清单

```text
[ ] 在 .env 设管理员账号（ADMIN_USERNAME / ADMIN_PASSWORD），首次启动自动建号，登录后立即改密码
[ ] 重置此前暴露过的吉客云 MCP Token（旧 Token 一律作废）
[ ] 填写新的吉客云 Token 到 .env
[ ] 设置页「立即测试连接」验证吉客云 MCP（initialize → tools/list）
[ ] 在“自动化”页立即同步商品，再同步 SKU/价格主档
[ ] 同步订单
[ ] 配置公司主体
[ ] 完成期初初始化（允许不平，差异进差异池）
[ ] 配置 SMTP 和财务邮箱
[ ] 创建 1688 开放平台应用，配置 OAuth 回调
[ ] 测试 1688 订单同步（每天 1 次 + 手动立即同步）
[ ] 上传浙江农信测试 XLSX（先 SHA256 版本化归档，再解析；旧 XLS 请先另存为 XLSX）
[ ] 在「吉客云导入」页上传客户端官方导出的 XLSX/CSV；首次上传先核对识别出的表头和报表类型
[ ] 生成测试财务 ZIP
[ ] 测试邮件发送（人工确认后）
[ ] 做数据库和 /data 备份
```

## 三个凭证开启后的下一步

| 凭证 | 开启后立即做 |
| --- | --- |
| 吉客云 MCP Token | 设置页验证 `initialize → tools/list`，再到自动化页依次立即同步“商品”“SKU/价格”；业务权限未开通时显示“业务权限未开通”，不会记成成功 |
| 1688 开放平台 OAuth | 回调配好后点「立即同步」跑一次，验证 OAuth 换 token 与订单拉取（只读，不下单） |
| 浙江农信 / SMTP | 上传测试 Excel/PDF 验证 SHA256 归档，再「生成测试 ZIP」走一遍财务包、最后「发送测试邮件」人工确认 |

顺序建议：吉客云主档 → 浙江农信资料 → 1688 订单 → SMTP 邮件。每一步成功后才会解锁下一环节的「未配置」占位。

## 吉客云客户端文件导入（开放平台 API 的替代路径）

当吉客云开放平台 API 未开通时，可在客户端使用**官方导出**，然后登录 8000 的「吉客云导入」页面上传 `.xlsx` 或 `.csv`。上传入口沿用既有账号权限，不会额外暴露无密码端口。

- 单文件最多 25 MiB、20,000 行；只接受 XLSX/CSV，旧 XLS 请在客户端另存为 XLSX
- 系统按 SHA256 去重，原件落在 `data/jackyun-exports/`，并把原始中文列名和行数据存入本地 staging
- 自动识别销售、售后、库存、商品/SKU、采购、入库、出库、仓库等常见表头；未知格式显示「待字段映射」，不会把猜测字段写入业务数据表
- 首次每一种报表上传后，核对页面的「类型」和「识别列」；以真实导出文件为准补充映射后，才接入相应看板数据

不读取吉客云客户端的 Token、Cookie、缓存或私有网络接口。

## 税务系统官方发票清单

在「税务发票」页上传税务系统下载的 `.xlsx` / `.csv` 清单，可选填所属月份。系统会保留原文件和每一行原始内容，并自动识别发票代码/号码、开票日期、购销方、金额、税额、价税合计、进销项和发票状态。

- 相同文件按 SHA256 幂等，不会重复写入；原件落在 `data/tax-invoices/`
- 清单明确给出关联订单号时，才会自动关联 1688 采购单或销售单；金额/名称相似但没有编号的记录进入「待核对」
- 某次清单没有出现发票，不等同于系统已经确认“未开票”；先核对清单所属期间和税务口径
- 采购详情页会同时显示由官方清单明确匹配的税票，完整台账可在 `/tax-invoices` 查看

## 目录结构

```text
backend/    FastAPI 应用（app/models 45+ 表、app/adapters 四个 Adapter、app/api/v1、Celery）
frontend/   Next.js 前端（含吉客云客户端文件上传入口，未落地的 Phase 明确占位不造假数据）
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
make migrate                                          # 应用迁移
cd backend && .venv/bin/alembic revision --autogenerate -m "..."  # 生成新迁移
```

## 备份 / 恢复

一键备份（pg_dump 自定义格式 + `/data` 原始文件 tar.gz，保留最近 14 份自动滚动）：

```bash
./scripts/backup.sh            # 产物落 backups/db_<时间戳>.dump + data_<时间戳>.tar.gz
./scripts/backup.sh --no-files # 只备数据库，跳过 /data
```

恢复：

```bash
# 数据库
source .env
PGPASSWORD="$POSTGRES_PASSWORD" pg_restore -h localhost -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists < backups/db_xxx.dump
# /data 原始文件
tar xzf backups/data_xxx.tar.gz -C /Users/gino/ecommerce-dashboard
```

## Celery 死信队列

Redis 无原生 DLX，采用「`task_reject_on_worker_lost` + `task_failure` 信号」等价实现：worker 崩溃（OOM/SIGKILL）时消息拒绝回队；任务重试耗尽后的最终失败会写入 Redis list `ecommerce:dead-letter`（保留 30 天），中间重试不落、避免噪声。

```bash
# 检视死信
redis-cli -n 0 LRANGE ecommerce:dead-letter 0 -1
# 手动重投某类任务（示例：吉客云销售同步）
cd backend && .venv/bin/python -c "from app.tasks.sync import sync_jackyun; sync_jackyun.delay('sales', True)"
```

注：失败的每一次尝试都已同时落库（`SyncLog` + 异常中心 `ensure_exception`），死信队列是补充的 Redis 侧可重投副本。

## 升级说明

```bash
# 1. 拉取最新代码，先备份（见上节）
git pull
# 2. 如修改了前端，重建静态产物；后端直接重启
make rebuild-fe   # 仅前端改动时需要
make restart
# 3. 应用新迁移（如有）
make migrate
# 4. 验证
make status
```

升级注意事项：

- **数据库结构变更一律走 Alembic 迁移**，禁止手改表结构；`alembic upgrade head` 幂等可重复执行
- **已发送给财务的 V1/V2 包不可覆盖**：升级不会触碰 `data/finance/*/output/` 已生成 ZIP（文件名带版本号天然隔离）
- **原始文件只读归档**：升级不影响 `data/finance/*/original/`，同名上传自动 version 递增
- **Celery 任务**：升级后 worker/beat 随 `make restart` 自动加载新代码；未配置的外部同步（吉客云/1688/SMTP）如实跳过并写日志
- **回滚**：代码回滚后执行 `make restart`；数据回滚用备份 SQL 恢复（注意备份时间点之后的写入会丢失，先确认）
