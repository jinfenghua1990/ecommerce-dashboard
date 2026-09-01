# 外部集成说明

## 吉客云（MCP）
- 传输：Streamable HTTP，POST JSON-RPC 2.0 到 `JACKYUN_MCP_URL`（`/mcp/messages`）
- 认证：`Authorization: Bearer <JACKYUN_MCP_TOKEN>`，会话经 `Mcp-Session-Id` 保持
- 白名单：仅调用已订阅的 13 个 method（见 `docs/jackyun-mapping.md`），禁止发明 API
- 安全：Token 只存服务器 .env；此前暴露的旧 Token 必须重置作废
- 行为：首次响应存 `raw_api_payloads`；限流 0.35s/次；失败写 `sync_logs` + 异常中心

## 1688 开放平台（Phase 4）
- OAuth 只读授权买家订单；每天同步 1 次 + 手动立即同步
- 不下单、不付款；未配置时页面显示"等待 1688 开放平台配置"
- 真实 method 以应用创建后的实际权限列表为准，不假定发票接口存在

## 浙江农信（文件导入）
- 不做 API 直联、不做网页 RPA；每月人工上传 XLSX/PDF/ZIP
- 落盘 `{DATA_DIR}/finance/{公司}/{年}/{月}/original/{类别}/`，SHA256 + 版本化，同名不覆盖

## 财务邮件（Phase 6）
- SMTP 人工确认后发送；月度 ZIP 原样交付；first/resent 发送记录分离
