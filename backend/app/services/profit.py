"""利润中心（规格 9）。

- 商品毛利 = 净销售收入 − 商品成本
- 成本优先级：实际采购结算 > 采购订单成本 > SKU默认成本 > 暂估成本
- 页面明确标识：实际成本 / 暂估成本 / 成本缺失；成本缺失时不显示假装精确的利润
- 贡献利润（含平台费用/推广/物流）仅在费用数据可靠后开放，本阶段不计算
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.audit import audit
from app.models.catalog import ProductSku
from app.models.profit import CostSnapshot
from app.models.sales import SalesOrder, SalesOrderItem
from app.utils.money import quantize, to_decimal

COST_PRIORITY = ("actual_cost", "purch_order_cost", "default_cost", "estimated_cost")
COST_LABEL = {
    "actual_cost": "实际采购结算成本",
    "purch_order_cost": "采购订单成本",
    "default_cost": "SKU默认成本",
    "estimated_cost": "暂估成本",
}


def effective_cost(snapshot: CostSnapshot) -> tuple[Decimal | None, str | None]:
    """按优先级取有效成本；无任何成本返回 (None, None)。"""
    for field in COST_PRIORITY:
        value = getattr(snapshot, field)
        if value is not None:
            return to_decimal(value), field
    return None, None


def gross_profit(net_sales: Decimal | None, cost: Decimal | None) -> Decimal | None:
    """净销售或成本缺失时返回 None，禁止假装精确。"""
    if net_sales is None or cost is None:
        return None
    return quantize(net_sales - cost, Decimal("0.01"))


def upsert_cost(
    db: Session, *,
    sku_id: int, period_year: int, period_month: int,
    actual_cost: str | None = None, purch_order_cost: str | None = None,
    default_cost: str | None = None, estimated_cost: str | None = None,
    source: str | None = None,
) -> CostSnapshot:
    """幂等 upsert；已有记录且值变化时 version+1，全部写审计日志。"""
    if not (1 <= period_month <= 12):
        raise ValueError("非法账期")
    if not all(v is None for v in (actual_cost, purch_order_cost, default_cost, estimated_cost)):
        if sum(v is not None for v in (actual_cost, purch_order_cost, default_cost, estimated_cost)) != 1:
            raise ValueError("每次只允许登记一种来源的成本")
    row = (
        db.query(CostSnapshot)
        .filter_by(sku_id=sku_id, period_year=period_year, period_month=period_month)
        .order_by(CostSnapshot.version.desc())
        .first()
    )
    values = {
        "actual_cost": to_decimal(actual_cost) if actual_cost is not None else None,
        "purch_order_cost": to_decimal(purch_order_cost) if purch_order_cost is not None else None,
        "default_cost": to_decimal(default_cost) if default_cost is not None else None,
        "estimated_cost": to_decimal(estimated_cost) if estimated_cost is not None else None,
    }
    if row:
        changed = any(
            (getattr(row, k) is None) != (v is None) or (v is not None and getattr(row, k) != v)
            for k, v in values.items()
        )
        if changed:
            row.version = (row.version or 1) + 1
        for k, v in values.items():
            setattr(row, k, v)
        if source:
            row.source = source
        db.commit()
        audit(db, "lan_user", "profit.cost.upsert", "cost_snapshots", row.id,
              {"skuId": sku_id, "period": f"{period_year}-{period_month:02d}", "values": values,
               "version": row.version})
        return row
    row = CostSnapshot(
        sku_id=sku_id, period_year=period_year, period_month=period_month,
        source=source or next((k for k, v in values.items() if v is not None), "estimated"),
        version=1, **values,
    )
    db.add(row)
    db.commit()
    audit(db, "lan_user", "profit.cost.create", "cost_snapshots", row.id,
          {"skuId": sku_id, "period": f"{period_year}-{period_month:02d}", "values": values})
    return row


def list_costs(db: Session, period_year: int | None = None, period_month: int | None = None) -> list[dict[str, Any]]:
    q = db.query(CostSnapshot)
    if period_year:
        q = q.filter(CostSnapshot.period_year == period_year)
    if period_month:
        q = q.filter(CostSnapshot.period_month == period_month)
    rows = q.order_by(CostSnapshot.period_year.desc(), CostSnapshot.period_month.desc(),
                      CostSnapshot.sku_id).all()
    out = []
    for r in rows:
        value, source = effective_cost(r)
        sku = db.get(ProductSku, r.sku_id)
        out.append({
            "id": r.id, "skuId": r.sku_id,
            "skuCode": sku.sku_code if sku else "",
            "skuName": sku.sku_name if sku else "",
            "period": f"{r.period_year}-{r.period_month:02d}",
            "values": {
                "actual": str(r.actual_cost) if r.actual_cost is not None else None,
                "purchOrder": str(r.purch_order_cost) if r.purch_order_cost is not None else None,
                "default": str(r.default_cost) if r.default_cost is not None else None,
                "estimated": str(r.estimated_cost) if r.estimated_cost is not None else None,
            },
            "effectiveCost": str(value) if value is not None else None,
            "effectiveSource": COST_LABEL[source] if source else None,
            "version": r.version,
        })
    return out


def overview(db: Session) -> dict[str, Any]:
    """成本覆盖情况（诚实空状态）；不含销售数据时毛利为空。"""
    rows = db.query(CostSnapshot).all()
    counts: dict[str, int] = {"actual_cost": 0, "purch_order_cost": 0, "default_cost": 0, "estimated_cost": 0, "missing": 0}
    for r in rows:
        _, source = effective_cost(r)
        counts[source or "missing"] += 1
    skus = db.query(ProductSku).count()
    return {
        "skuCount": skus,
        "snapshotCount": len(rows),
        "coverage": counts,
        "contributionProfitEnabled": False,  # 规格：费用数据可靠后才开放
    }


def compute(db: Session, period_year: int, period_month: int) -> dict[str, Any]:
    """按账期计算商品毛利；净销售取已支付订单 item(amount - discount)；成本缺失返回 None + costMissing。"""
    items = (
        db.query(SalesOrderItem)
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
        .filter(SalesOrder.order_status.in_(["paid", "done", "finished", "已支付", "已完成"]))
        .filter(
            (SalesOrderItem.amount.isnot(None)) | (SalesOrderItem.discount_amount.isnot(None))
        )
        .all()
    )
    net_sales = Decimal("0")
    sku_rows: dict[int, list[Decimal]] = {}
    for it in items:
        net = (it.amount or Decimal("0")) - (it.discount_amount or Decimal("0"))
        if net > 0:
            net_sales += net
            if it.sku_id:
                sku_rows.setdefault(it.sku_id, []).append(net)
    costs = db.query(CostSnapshot).filter_by(period_year=period_year, period_month=period_month).all()
    cost_by_sku = {}
    for c in costs:
        value, _ = effective_cost(c)
        if value is not None:
            cost_by_sku.setdefault(c.sku_id, value)
    goods_cost = Decimal("0")
    missing_skus = set()
    for sku_id, nets in sku_rows.items():
        if sku_id in cost_by_sku:
            goods_cost += cost_by_sku[sku_id] * sum(nets)
        else:
            missing_skus.add(sku_id)
    net_sales = quantize(net_sales, Decimal("0.01"))
    goods_cost = quantize(goods_cost, Decimal("0.01"))
    cost_missing = bool(missing_skus) or net_sales == 0 and goods_cost == 0 and bool(sku_rows)
    return {
        "period": f"{period_year}-{period_month:02d}",
        "netSales": str(net_sales) if sku_rows else None,
        "goodsCost": str(goods_cost) if sku_rows else None,
        "grossProfit": str(gross_profit(net_sales, goods_cost)) if sku_rows and not missing_skus else None,
        "costMissingSkus": sorted(missing_skus),
        "costMissing": cost_missing,
        "note": "成本缺失的 SKU 已计入 costMissingSkus；毛利仅在实际成本覆盖时输出。",
    }
