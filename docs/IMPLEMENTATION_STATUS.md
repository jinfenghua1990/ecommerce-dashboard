# 实施状态

最后更新：2026-09-01（按原规格技术栈重写版）

## 当前结论

- 本仓库为**按规格 MD 原栈（FastAPI + Next.js + Celery）的重写版**；此前的 TS monorepo（`~/Documents/ChatGPT/电商工作平台/`）Phase 0 已验证可用，用户决策于 2026-09-01 改回规格原栈重写，TS 版保留未删除。
- 新仓库：`/Users/gino/ecommerce-dashboard`，端口沿用 **18080**。
- 局域网信任模式沿用（无账户/无密码/无 Cookie），RBAC 表结构已建、运行链路未启用，未来公网化必须先恢复认证。

## Phase 状态

| Phase | 状态 | 说明 |
|---|---|---|
| Phase 0 工程基础 | 已完成 | monorepo / compose / 迁移 / healthz / 审计 / .env 边界 / 45+ 表模型 / 4 Adapter 骨架 / beat schedule / 单测 |
| Phase 1 吉客云 | **代码就绪，数据侧阻塞** | MCP 客户端已实现；12 个 sync 方法已实现通用拉取骨架（真实 tools/call → raw payload 存档 → 动态幂等 upsert，字段按真实响应映射不猜死）；`tasks.sync_jackyun` 支持全部 12 种同步类型。工具调用被吉客云侧拦截 `0130000609`，开通后无需改代码 |
| Phase 2 经营看板 | **核心完成** | `/api/v1/dashboard/*`（销售趋势/平台排行/SKU 排行/库存/订单/售后）+ 销售页、商品与库存页接真实 API（本地库聚合，空态如实显示）；总览 9 指标改为本地库真实聚合（`dashboard.overview_metrics`） |
| Phase 3 采购/1688 | **核心完成** | 手工登记真实 1688 订单（幂等）→ 多 SKU 分配 → 附加费用 → 金额平衡校验（未分配≠0 禁确认）→ 状态机 8 态 → 吉客云采购单关联 → 发票多对多 + 状态分离 + 超额拦截已上线；1688 OAuth/自动同步仍等凭证 |
| Phase 4 浙江农信+财务资料 | **核心完成** | 上传归档（SHA256+版本化+同名不覆盖）/ 完整性检查（INCOMPLETE/READY）/ 原样 ZIP 打包（V1/V2 不可覆盖）/ 下载 已上线并 E2E 验证；SMTP 发送与银行文件解析仍阻塞 |
| Phase 5 回款+利润 | **核心完成** | 回款：对方户名→平台映射规则（可配置+审计日志）/ 银行流水指纹幂等登记 / 应收登记 / 综合评分匹配（金额40+平台30+账期15+户名10）/ 确认→刷新应收状态 / 应回款·已回款·待回款分平台总览。利润：成本快照（优先级 实际>采购订单>默认>暂估，版本化管理）/ 毛利计算（净销售−商品成本，成本缺失返回 None 不假装精确）/ 贡献利润未开放（费用数据可靠后才启用）。前端回款页+利润页已接真实 API |
| Phase 6 期初+异常+月结 | **核心完成** | 异常中心 API/页面可用；期初初始化向导（`/api/v1/opening/*`：7 类期初 + 差异池 + 审计日志）已上线（设置页）；月结快照 `/api/v1/closing/*`（V1 保留、重算产生 V2/V3，快照含毛利/回款指标）已上线（利润页）；自动化页改为展示真实 beat schedule + 同步任务/日志；`monthly_verify` 月初完整校验已实现（缺资料自动进异常中心）；SMTP 发送仍阻塞 |

## 外部阻塞

| 项目 | 状态 | 所需动作 |
|---|---|---|
| 吉客云 MCP | **凭证已提供（2026-09-01）** | AppKey 89334156 + 新 MCP Token 已写入 .env（未入 Git）；连接测试见下文验证记录。**业务数据调用需联系客户经理开通"开放平台"API 权限（subCode 0130000609）** |
| 1688 | 阻塞 | 创建开放平台应用 → 提供 AppKey/Secret + 回调地址 |
| 浙江农信解析 | 部分阻塞 | 提供脱敏 Excel/PDF 样本后实现解析器 |
| SMTP | 阻塞 | 提供邮件服务账号 + 收件人 + 人工确认规则 |

## 验证记录

### 2026-09-01 Phase 0（重写版）

- `docker compose config` 通过；postgres:17.11-alpine / redis:7.4-alpine（本机已有镜像，Docker Hub 直连被墙，python 基础镜像经 daocloud 镜像拉取）
- Alembic 初始迁移 autogenerate 生成并 upgrade head（见迁移文件 0001）
- 单元测试：金额 Decimal（拒绝 float）、采购分配平衡校验（规格 7.4 示例 50000 分配）、费用分摊尾差归零、银行文件 SHA256/文件名消毒 —— 全部通过
- 18080 端口：从旧 TS 版切换到新栈后验证

### 吉客云连接测试结果（2026-09-01 真实调用）

| 步骤 | 结果 |
|---|---|
| MCP 传输 | ✅ Streamable HTTP POST `/mcp/messages`；认证为 `Authorization: <裸Token>`（**不带 Bearer 前缀**，实测 8 种 header 变体确认） |
| initialize | ✅ serverInfo = `open-platform-mcp v1.0.0` |
| tools/list | ✅ 返回 14 个工具，与订阅清单 1:1 对应（tool 名与 inputSchema 见 `docs/jackyun-samples.json`） |
| tools/call（业务数据） | ❌ **真实阻塞**：`code 0130000609 "该应用未开通开放平台，无法调用接口，请联系客户经理处理"` |

**用户下一步最小动作：联系吉客云客户经理，为企业应用开通"开放平台"API 调用权限**（MCP 授权已完成，差业务侧开通）。开通后无需改代码，重测即可拉到真实字段样本。

### 验证记录（补充）

- 单元测试 14/14 通过（Decimal 拒绝 float、分配平衡、分摊尾差归零、SHA256/文件名消毒）
- 18080 本机 + 局域网均 200；6 服务全部 healthy
- 前端设置页「立即测试连接」可复现以上结果；状态徽章：吉客云 MCP = 已连接

### 2026-09-01 Phase 5（回款 + 利润）

- 后端：`services/reconciliation.py`（匹配框架/规则/指纹/确认）、`api/v1/reconciliation.py`、`services/profit.py`（成本优先级/毛利诚实语义）、`api/v1/profit.py`；路由已挂载 /api/v1/reconciliation/*、/api/v1/profit/*
- 单测 47/47 通过（新增回款 8 项 + 利润 8 项：成本优先级、毛利 None 语义、多来源校验、指纹稳定/摘要参与 fallback）
- 前端：回款页（指标卡/规则管理/应收登记/匹配建议确认拒绝）、利润页（成本登记/覆盖统计/毛利计算）接真实 API，无模拟数据
- 银行流水登记入口在回款页；浙江农信 XLSX/PDF 解析器仍等脱敏样本（Phase 4 阻塞项）

### 2026-09-01 Phase 2 + Phase 6（经营看板 / 期初 / 月结）

- 后端新增：`services/dashboard.py` + `/api/v1/dashboard/*`（销售趋势/平台排行/SKU 排行/库存/订单/售后）；`services/opening.py` + `/api/v1/opening/*`（7 类期初 + 差异池 + 调整审计）；`services/closing.py` + `/api/v1/closing/*`（月结快照 V1 保留、重算产生 V2/V3）；`/api/v1/automation/*`（beat schedule/任务/日志展示）
- `system.overview` 9 指标改为本地库真实聚合（空态如实 None），phase 更新为 6
- 吉客云 12 个 sync 方法实现通用拉取骨架（raw 存档 + 动态幂等 upsert，不猜字段）；`tasks.sync_jackyun` 支持全部同步类型；`monthly_verify` 月初完整校验实现
- 前端：销售页/商品与库存页/自动化页接真实 API；设置页加期初初始化向导；利润页加月结快照区
- 新增单测 6 项（金额平衡规格示例/分摊尾差/float 拒绝）—— 纯逻辑层

## 技术决策记录

1. **重写回规格原栈**（2026-09-01，用户决策）：TS 版与规格 MD 栈不一致；用户确认趁 Phase 0 早期重写成本低。
2. **局域网信任模式保留**：来自 TS 版已验证决策；RBAC 结构保留在 models/org.py。
3. **镜像策略**：Docker Hub 直连超时 → 复用本机已拉取镜像标签 + daocloud 镜像拉取 python:3.12-slim + pip 走清华源。
4. **MCP 传输**：按 Streamable HTTP（POST JSON-RPC 至 /mcp/messages，Bearer 认证，Mcp-Session-Id 会话保持）；如真实响应异常将记录于上文验证记录。

## 下一步

- **等待外部动作（唯一真实阻塞）**：联系吉客云客户经理开通开放平台 API（subCode 0130000609）→ 开通后 Phase 1 同步方法即自动生效拉取真实数据 → 经营看板/利润自动出数
- Phase 4 剩余：银行 XLSX/PDF 解析器（等脱敏样本）、SMTP 配置后开放发送链路（first/resent 记录已建）
- Phase 3：1688 应用创建后实现 OAuth 回调与订单拉取
- Phase 5 剩余：销售数据（吉客云）落地后校验毛利计算；贡献利润待费用数据可靠后开放
- 自动化频率后台可配置化（当前 beat schedule 频率固定，配置界面列入下一迭代）
