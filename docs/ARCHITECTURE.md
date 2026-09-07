# 电商经营数据平台 — 架构基线（2026）

> 本文件是仓库的架构事实源。Codex / AI Agent / 人工开发在修改代码前应优先遵守本文件；历史页面、旧脚本或旧注释与本文件冲突时，以本文件为准。

## 1. 系统定位

这是一个面向单公司经营、供应链与财务月结的 **模块化单体（Modular Monolith）**，不是实时 ERP，也不拆微服务。

核心时间口径：**自然月**。

核心业务链路：

`库存 → 补货建议 → 补货计划 → 工厂生产 / 直接采购 → 耗材备料 → 在途 → 到货验收 → 入库 → 月度经营 / 财务月结`

采购执行可按日跟进；1688 可每天同步一次。经营、利润、回款、结存和正式财务输出必须有明确月份，不得混入其他月份数据。

业务时区统一：`Asia/Shanghai`。

## 2. 唯一运行架构

### 对外入口

**整个业务系统只有一个公开应用端口：8000。**

- FastAPI 监听 `8000`
- API 路径：`/api/v1/...`
- Next.js 使用 `output: "export"` 生成 `frontend/out`
- FastAPI 同端口托管 `frontend/out`
- 不运行独立 `next start` 生产服务
- 不为新增业务模块创建 8001 / 9001 / 其他独立端口

新增功能一律通过现有项目路由区分，例如：

- `/supply-chain`
- `/supply-chain/production`
- `/supply-chain/material-flow`
- `/purchase/workbench`

PostgreSQL / Redis 只属于基础设施，不作为公开业务端口。

## 3. 技术栈

### Frontend

- TypeScript
- React 19
- Next.js 16
- Tailwind CSS 4
- 静态导出，不承担生产 API Server 职责

### Backend

- Python 3.12（当前稳定基线；升级 Python 大版本必须单独兼容测试）
- FastAPI
- Pydantic
- SQLAlchemy 2
- Alembic

### Data / Jobs

- PostgreSQL 17
- Redis 7.x
- Celery Worker + Beat

不要为了“技术更新”改写为微服务、Kafka、Kubernetes、Go/Rust/Java 多语言系统，除非现有模块化单体已经出现有证据的容量或隔离瓶颈。

## 4. 数据事实源

- 吉客云：正品商品 / SKU 主档和相关业务事实来源
- 本系统：耗材主档、耗材库存流水、生产订单、生产耗材预占与流转
- 1688：已下单采购交易来源；不自动下单、不自动付款
- 银行：文件导入原始流水
- 税务 / 发票：导入并保留原始事实与审计关系

同一个业务事实不得为了新页面再建第二套平行表或第二套计算逻辑。优先复用已有模型、service 和关联表。

## 5. 月度口径红线

正式经营 / 财务数据必须显式属于 `year + month`。

统一自然月边界函数：`app.services.monthly_core.month_bounds()`。

禁止：

- 月结函数接收 year/month，却内部读取全历史累计
- 一个模块按 UTC 月份、另一个模块按 Asia/Shanghai 月份
- 首页“经营总览”用全历史累计冒充本月
- 不同页面使用同名指标却维护两套未经定义的公式

当前仍需继续统一的业务定义：**经营净销售 vs 利润净销售公式**。在正式统一前不得为了让数字“看起来一样”而偷偷修改会计/经营含义。

## 6. 金额与数量

- 数据库金额：`Numeric/Decimal`
- Python 业务计算：`Decimal`
- 禁止用二进制 `float` 参与金额计算
- API 金额优先序列化为精确字符串
- 数量同样优先使用 `Numeric(18,4)` / `Decimal`

缺数据时返回 `None` / 明确缺失项，不生成伪精确数字。

## 7. 供应链库存规则

### 正品

主要库存事实来自吉客云快照。

### 耗材

三口径：

- 自有仓 `stock_qty`
- 在途 `transit_qty`
- 工厂库存 `factory_qty`

生产单创建：只预占，不扣物理库存。

发工厂：自有仓减少、在途增加。

工厂签收：在途减少、工厂库存增加。

生产实际消耗：后续从工厂库存按真实消耗扣减。

所有真实库存变化必须留下不可覆盖流水；关键写操作必须幂等。

## 8. 数据完整性与并发

高风险业务写入至少满足：

- 数据库唯一约束 / CheckConstraint 能覆盖的尽量下沉数据库
- request key 幂等
- 库存扣减 / 预占使用事务
- 并发库存修改使用行锁（`SELECT ... FOR UPDATE`）
- 不允许“先查库存 → 无锁扣减”的竞态写法
- 不物理删除已形成审计链的业务事实，除非规格明确允许

老表补 ForeignKey 前必须先做 orphan audit，禁止直接加约束导致真实库迁移失败。

## 9. 认证与安全

- 默认 `ACCESS_MODE=rbac`
- `APP_SECRET_KEY` 只来自 `.env` / 环境变量
- 密码使用 PBKDF2-HMAC-SHA256；当前新 hash 工作因子 600,000
- 旧 hash 成功登录后自动升级
- 修改密码 / 退出登录通过 `token_version` 撤销旧令牌
- 转公网前必须增加 TLS、严格网络白名单，并重新评估浏览器令牌存储方式

`.env`、业务 `data/`、数据库备份不得进入 Docker build context 或 Git。

## 10. 部署与验证

### macOS 主运行方式

- `scripts/native-start.sh`
- FastAPI + worker + beat
- `frontend/out` 缺失时构建
- 业务入口：`http://localhost:8000`

### Docker 灾备 / 迁移方式

根目录 `Dockerfile` 构建：

1. Node 24 构建 Next 静态输出
2. Python 3.12 构建 FastAPI runtime
3. 将 `frontend/out` 放入统一镜像
4. FastAPI 唯一监听 8000

`docker-compose.yml` 不再运行独立 frontend 容器。

## 11. CI 必须守住的门槛

Pull Request 必须验证：

- Fresh PostgreSQL 全量 Alembic migration
- Runtime undefined-name scan
- 供应链 / 月度核心回归
- Full backend pytest
- Frontend TypeScript typecheck
- Next.js production static build，并确认 `out/index.html`
- Docker Compose topology 校验
- 单端口生产 Docker image 实际 build

运行级错误和完整测试失败必须阻塞合并；普通未使用 import 等卫生告警可逐步清理后再升级为全阻塞。

## 12. 代码组织原则

- 新页面不得复制已有 service 形成第二套事实
- 一个文件明显过大时按业务模块逐步拆分，不做一次性大爆炸重写
- 旧路由先保留兼容跳转，再逐步删除旧实现
- Git 历史负责版本恢复，源码目录不要保留 `*.bak-*` 手工备份
- 所有新增模块继续在 feature branch 开发，经 CI 后再合并 master

当前重点技术债：

1. `/purchase/workbench/page.tsx` 体积过大，应逐步模块化
2. 历史 procurement 路由实现需要在兼容跳转稳定后清理
3. 老耗材关联表补 FK 前先运行 orphan audit
4. 经营净销售 / 利润净销售统一指标定义
5. 建立自动备份调度与定期 `make restore-check` 恢复演练
