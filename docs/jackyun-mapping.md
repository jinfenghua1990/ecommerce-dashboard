# 吉客云字段 mapping（Phase 1 进行中）

> 原则：不猜字段。以下 mapping 依据 `raw_api_payloads` 中的真实首次响应填写。

## 已订阅接口白名单

```text
ass-business.returnchange.fullinfoget   售后全量
erp-goods.pricelist.get                 价格
erp.allocate.get                        调拨
erp.purch.get                           采购单
erp.purchordersett.get                  采购结算
erp.purchreturn.get                     采购退货
erp.stockquantity.get                   库存
erp.storage.goodsdocin.v2               入库单
erp.storage.goodsdocout.v2              出库单
erp.storage.goodslist                   商品档案
erp.warehouse.get                       仓库
oms.trade.fullinfoget                   线上订单全量
omsapi-business.order.get               OMS 订单
wms.order.query-info.page               发货单
```

## 字段映射表

| 目标表.字段 | 吉客云字段 | 状态 |
|---|---|---|
| （待真实响应样本后填写） | | ⏳ Phase 1 |

## 实际响应与预期差异记录

（任何真实 API 响应与文档不一致处记录于此，不偷偷改业务定义）

## MCP 工具清单（2026-09-01 tools/list 实测，open-platform-mcp v1.0.0）

| API method | MCP tool | 必填入参 |
|---|---|---|
| erp.storage.goodsdocin.v2 | getGoodsDocInListInfo | pageSize, selelctFields |
| erp.storage.goodsdocout.v2 | getGoodsDocOutListInfo | pageSize, selelctFields |
| erp.storage.goodslist | getGoodsListInfoByGoodsNo | pageSize |
| erp-goods.pricelist.get | getGoodsPriceListInfo | pageSize, cols |
| erp.stockquantity.get | getGoodsStockQuantityListInfo | pageSize |
| omsapi-business.order.get | getOrderListInfo | pageSize |
| erp.purch.get | getPurchOrderListInfo | pageSize |
| erp.purchreturn.get | getPurchOrderReturnListInfo | pageSize |
| erp.purchordersett.get | getPurchOrderSettleListInfo | pageSize, cols |
| ass-business.returnchange.fullinfoget | getReturnChangeListInfo | pageSize |
| wms.order.query-info.page | getShopOrderLiseInfo | pageSize, fields |
| oms.trade.fullinfoget | getTradesListInfo | fields, pageSize |
| erp.allocate.get | getStockAllocateListInfo | pageSize |
| erp.warehouse.get | getWarehouseListInfo | pageSize |

## 认证（实测确认）

- `Authorization: <裸Token>`，**不带 Bearer 前缀**（Bearer 形式返回 401 Invalid token）
- 会话经 `Mcp-Session-Id` 响应头保持
- 传输：POST JSON-RPC 2.0 到 `https://mcp.open.jackyun.com/mcp/messages`，Accept: `application/json, text/event-stream`

## 首次 tools/call 真实响应（2026-09-01）

三个只读接口均返回：

```json
{"code":0,"msg":"该应用未开通开放平台，无法调用接口，请联系客户经理处理","result":{"data":null},"subCode":"0130000609"}
```

**结论**：MCP 通道正常，业务侧需联系吉客云客户经理开通开放平台 API 权限。开通前字段 mapping 保持待填。
