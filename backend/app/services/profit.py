"""利润中心（规格 9）。

- 商品毛利 = 净销售收入 − 商品成本
- 成本优先级：实际采购结算 > 采购订单成本 > SKU 默认成本 > 暂估成本
- 一个 SKU/账期允许并存多种成本来源，按优先级选择生效值
- SKU、销量或成本缺失时不输出伪精确的商品成本和毛利
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import or_
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
SOURCE_ALIASES = {
    "actual": "actual_cost", "settlement": "actual_cost", "actual_cost": "actual_cost",
    "purch": "purch_order_cost", "order": "purch_order_cost", "purch_order_cost": "purch_order_cost",
    "default": "default_cost", "default_cost": "default_cost",
    "estimated": "estimated_cost", "estimated_cost": "estimated_cost",
}


def effective_cost(snapshot: CostSnapshot) -> tuple[Decimal | None, str | None]:
    """按优先级取单条快照的有效成本。"""
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


def _combined_cost(
    rows: list[CostSnapshot], sku_default: Decimal | None = None
) -> tuple[dict[str, Decimal | None], Decimal | None, str | None]:
    """合并同一 SKU/账期的多来源记录，每个来源使用其最新版本。"""
    values: dict[str, Decimal | None] = {field: None for field in COST_PRIORITY}
    ordered = sorted(rows, key=lambda row: (row.version or 0, row.id or 0), reverse=True)
    for field in COST_PRIORITY:
        for row in ordered:
            value = getattr(row, field)
            if value is not None:
                values[field] = to_decimal(value)
                break
    if values["default_cost"] is None and sku_default is not None:
        values["default_cost"] = to_decimal(sku_default)
    for field in COST_PRIORITY:
        if values[field] is not None:
            return values, values[field], field
    return values, None, None


def upsert_cost(
    db: Session,
    *,
    sku_id: int,
    period_year: int,
    period_month: int,
    actual_cost: str | None = None,
    purch_order_cost: str | None = None,
    default_cost: str | None = None,
    estimated_cost: str | None = None,
    source: str | None = None,
    actor: str = "system",
) -> CostSnapshot:
    """按来源幂等 upsert；不同来源并存，单一来源变更时版本号递增。"""
    if not (1 <= period_month <= 12):
        raise ValueError("非法账期")
    raw_values = {
        "actual_cost": actual_cost,
        "purch_order_cost": purch_order_cost,
        "default_cost": default_cost,
        "estimated_cost": estimated_cost,
    }
    provided = [field for field, value in raw_values.items() if value is not None]
    if len(provided) != 1:
        raise ValueError("每次必须且只允许登记一种来源的成本")
    chosen = provided[0]
    if source:
        canonical = SOURCE_ALIASES.get(source)
        if canonical is None:
            raise ValueError("未知成本来源")
        if canonical != chosen:
            raise ValueError("成本来源与填写字段不一致")
    sku = db.get(ProductSku, sku_id)
    if sku is None:
        raise ValueError("SKU 不存在，请先同步吉客云商品主档")
    value = to_decimal(raw_values[chosen])
    if value < 0:
        raise ValueError("成本不能为负数")

    candidates = (
        db.query(CostSnapshot)
        .filter_by(sku_id=sku_id, period_year=period_year, period_month=period_month)
        .order_by(CostSnapshot.version.desc(), CostSnapshot.id.desc())
        .all()
    )
    row = next(
        (item for item in candidates
         if SOURCE_ALIASES.get(item.source, item.source) == chosen
         or getattr(item, chosen) is not None),
        None,
    )
    if row:
        if getattr(row, chosen) != value:
            row.version = (row.version or 1) + 1
        for field in COST_PRIORITY:
            setattr(row, field, value if field == chosen else None)
        db.commit()
        audit(
            db, actor, "profit.cost.upsert", "cost_snapshots", row.id,
            {"skuId": sku_id, "period": f"{period_year}-{period_month:02d}",
             "source": chosen, "value": str(value), "version": row.version},
        )
        return row

    values = {field: value if field == chosen else None for field in COST_PRIORITY}
    row = CostSnapshot(
        sku_id=sku_id, period_year=period_year, period_month=period_month,
        source=chosen, version=1, **values,
    )
    db.add(row)
    db.commit()
    audit(
        db, actor, "profit.cost.create", "cost_snapshots", row.id,
        {"skuId": sku_id, "period": f"{period_year}-{period_month:02d}",
         "source": chosen, "value": str(value)},
    )
    return row


def list_costs(
    db: Session, period_year: int | None = None, period_month: int | None = None
) -> list[dict[str, Any]]:
    q = db.query(CostSnapshot)
    if period_year:
        q = q.filter(CostSnapshot.period_year == period_year)
    if period_month:
        q = q.filter(CostSnapshot.period_month == period_month)
    rows = q.order_by(
        CostSnapshot.period_year.desc(), CostSnapshot.period_month.desc(), CostSnapshot.sku_id
    ).all()
    grouped: dict[tuple[int, int, int], list[CostSnapshot]] = {}
    for row in rows:
        grouped.setdefault((row.sku_id, row.period_year, row.period_month), []).append(row)
    sku_ids = {key[0] for key in grouped}
    skus = (
        {sku.id: sku for sku in db.query(ProductSku).filter(ProductSku.id.in_(sku_ids)).all()}
        if sku_ids else {}
    )

    out = []
    for (sku_id, year, month), source_rows in grouped.items():
        sku = skus.get(sku_id)
        values, value, source = _combined_cost(source_rows, sku.default_cost if sku else None)
        out.append({
            "id": max(row.id for row in source_rows), "skuId": sku_id,
            "skuCode": sku.sku_code if sku else "", "skuName": sku.sku_name if sku else "",
            "period": f"{year}-{month:02d}",
            "values": {
                "actual": str(values["actual_cost"]) if values["actual_cost"] is not None else None,
                "purchOrder": str(values["purch_order_cost"]) if values["purch_order_cost"] is not None else None,
                "default": str(values["default_cost"]) if values["default_cost"] is not None else None,
                "estimated": str(values["estimated_cost"]) if values["estimated_cost"] is not None else None,
            },
            "effectiveCost": str(value) if value is not None else None,
            "effectiveSource": COST_LABEL[source] if source else None,
            "version": max(row.version or 1 for row in source_rows),
        })
    return out


def overview(db: Session) -> dict[str, Any]:
    """按唯一 SKU/账期统计成本覆盖；不同来源记录不重复计算覆盖。"""
    rows = db.query(CostSnapshot).all()
    counts: dict[str, int] = {
        "actual_cost": 0, "purch_order_cost": 0, "default_cost": 0,
        "estimated_cost": 0, "missing": 0,
    }
    grouped: dict[tuple[int, int, int], list[CostSnapshot]] = {}
    for row in rows:
        grouped.setdefault((row.sku_id, row.period_year, row.period_month), []).append(row)
    covered_skus: set[int] = set()
    for (sku_id, _year, _month), source_rows in grouped.items():
        _, value, source = _combined_cost(source_rows)
        counts[source or "missing"] += 1
        if value is not None:
            covered_skus.add(sku_id)

    sku_rows = db.query(ProductSku).all()
    for sku in sku_rows:
        if sku.id not in covered_skus and sku.default_cost is not None:
            counts["default_cost"] += 1
            covered_skus.add(sku.id)
    counts["missing"] += max(len(sku_rows) - len(covered_skus), 0)
    return {
        "skuCount": len(sku_rows), "snapshotCount": len(rows), "coverage": counts,
        "contributionProfitEnabled": False,
    }


def compute(db: Session, period_year: int, period_month: int) -> dict[str, Any]:
    """按账期计算商品毛利；单位成本乘销量，缺失项明确返回。"""
    if not (1 <= period_month <= 12):
        raise ValueError("非法账期")
    period_start = datetime(period_year, period_month, 1, tzinfo=timezone.utc)
    next_start = (
        datetime(period_year + 1, 1, 1, tzinfo=timezone.utc)
        if period_month == 12 else datetime(period_year, period_month + 1, 1, tzinfo=timezone.utc)
    )
    items = (
        db.query(SalesOrderItem)
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
        .filter(or_(
            SalesOrder.order_status.in_(["paid", "done", "finished", "已支付", "已完成"]),
            SalesOrder.pay_status.in_(["paid", "success", "已支付"]),
        ))
        .filter(SalesOrder.ordered_at >= period_start, SalesOrder.ordered_at < next_start)
        .filter((SalesOrderItem.amount.isnot(None)) | (SalesOrderItem.discount_amount.isnot(None)))
        .all()
    )

    net_sales = Decimal("0")
    sku_quantities: dict[int, Decimal] = {}
    unmapped_items: list[int] = []
    quantity_missing_items: list[int] = []
    for item in items:
        net_sales += (item.amount or Decimal("0")) - (item.discount_amount or Decimal("0"))
        if not item.sku_id:
            unmapped_items.append(item.id)
        elif item.quantity is None:
            quantity_missing_items.append(item.id)
        else:
            sku_quantities[item.sku_id] = sku_quantities.get(item.sku_id, Decimal("0")) + item.quantity

    cost_rows = db.query(CostSnapshot).filter_by(
        period_year=period_year, period_month=period_month
    ).all()
    grouped_costs: dict[int, list[CostSnapshot]] = {}
    for row in cost_rows:
        grouped_costs.setdefault(row.sku_id, []).append(row)
    skus = (
        {sku.id: sku for sku in db.query(ProductSku)
         .filter(ProductSku.id.in_(sku_quantities.keys())).all()}
        if sku_quantities else {}
    )

    goods_cost = Decimal("0")
    missing_skus: set[int] = set()
    for sku_id, quantity in sku_quantities.items():
        sku = skus.get(sku_id)
        if sku is None:
            missing_skus.add(sku_id)
            continue
        _, unit_cost, _ = _combined_cost(grouped_costs.get(sku_id, []), sku.default_cost)
        if unit_cost is None:
            missing_skus.add(sku_id)
        else:
            goods_cost += unit_cost * quantity

    net_sales = quantize(net_sales, Decimal("0.01"))
    goods_cost = quantize(goods_cost, Decimal("0.01"))
    incomplete = bool(missing_skus or unmapped_items or quantity_missing_items)
    has_items = bool(items)
    return {
        "period": f"{period_year}-{period_month:02d}",
        "netSales": str(net_sales) if has_items else None,
        "goodsCost": str(goods_cost) if has_items and not incomplete else None,
        "grossProfit": str(gross_profit(net_sales, goods_cost))
        if has_items and not incomplete else None,
        "costMissingSkus": sorted(missing_skus),
        "unmappedItems": sorted(unmapped_items),
        "quantityMissingItems": sorted(quantity_missing_items),
        "costMissing": incomplete,
        "note": "单位成本按销售数量计算；SKU、数量或成本任一缺失时，商品成本与毛利均不输出。",
    }
