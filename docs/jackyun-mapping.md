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
