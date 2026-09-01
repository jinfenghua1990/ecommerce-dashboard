"""经营看板（规格 6）：销售趋势 / 平台店铺排行 / SKU 排行 / 订单 / 售后 / 库存。

原则：
- 页面只查本地库（吉客云同步副本），不触发外部查询（规格 5）
- 数据未落地时如实返回空集，不伪造数字
- 全部金额 Decimal，输出 str
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.catalog import InventorySnapshot, ProductSku, Store
from app.models.sales import AftersalesOrder, SalesOrder, SalesOrderItem
from app.utils.money import money_str, to_decimal


def _money(value: Decimal | None) -> str | None:
    return money_str(value) if value is not None else None


def sales_trend(db: Session, days: int = 30) -> list[dict[str, Any]]:
    """近 N 天每日销售额/订单数/退款率。按 ordered_at 分组（本地库）。"""
    since = datetime.now() - timedelta(days=days)
    rows = (
        db.query(
            func.date(SalesOrder.ordered_at),
            func.count(SalesOrder.id),
            func.coalesce(func.sum(SalesOrder.paid_amount), 0),
        )
        .filter(SalesOrder.ordered_at >= since)
        .group_by(func.date(SalesOrder.ordered_at))
        .order_by(func.date(SalesOrder.ordered_at))
        .all()
    )
    out = []
    for day, cnt, amt in rows:
        out.append({"date": day.isoformat(), "orders": int(cnt), "salesAmount": _money(amt)})
    return out


def platform_ranking(db: Session) -> list[dict[str, Any]]:
    """平台/店铺销售排行。无平台字段的订单归入 unknown。"""
    rows = (
        db.query(SalesOrder.platform, func.count(SalesOrder.id), func.coalesce(func.sum(SalesOrder.paid_amount), 0))
        .group_by(SalesOrder.platform)
        .order_by(func.sum(SalesOrder.paid_amount).desc())
        .all()
    )
    out = []
    for platform, cnt, amt in rows:
        out.append({"platform": platform or "unknown", "orders": int(cnt), "salesAmount": _money(amt)})
    return out


def sku_ranking(db: Session, limit: int = 20) -> list[dict[str, Any]]:
    """SKU 销售额排行（来自订单明细本地副本）。"""
    rows = (
        db.query(
            SalesOrderItem.sku_code,
            SalesOrderItem.goods_name,
            func.count(SalesOrderItem.id),
            func.coalesce(func.sum(SalesOrderItem.amount), 0),
        )
        .group_by(SalesOrderItem.sku_code, SalesOrderItem.goods_name)
        .order_by(func.sum(SalesOrderItem.amount).desc())
        .limit(limit)
        .all()
    )
    out = []
    for sku_code, goods_name, cnt, amt in rows:
        out.append({
            "skuCode": sku_code or "",
            "goodsName": goods_name or "",
            "orders": int(cnt),
            "salesAmount": _money(amt),
        })
    return out


def inventory_summary(db: Session) -> dict[str, Any]:
    """库存概览：SKU 总数 / 有快照 SKU / 库存总量 / 各仓库。快照取最新一条。"""
    skus = db.query(ProductSku).count()
    snap = (
        db.query(InventorySnapshot)
        .order_by(InventorySnapshot.snapshot_at.desc())
        .first()
    )
    if not snap:
        return {"skuCount": skus, "snapshotAt": None, "totalQuantity": None,
                "byWarehouse": [], "note": "库存快照未同步（吉客云库存同步落地后显示）"}
    latest_at = snap.snapshot_at
    rows = (
        db.query(
            InventorySnapshot.warehouse_id,
            func.coalesce(func.sum(InventorySnapshot.quantity), 0),
            func.count(func.distinct(InventorySnapshot.sku_id)),
        )
        .filter(InventorySnapshot.snapshot_at == latest_at)
        .group_by(InventorySnapshot.warehouse_id)
        .all()
    )
    total = Decimal("0")
    by_warehouse = []
    for wid, qty, sku_cnt in rows:
        total += qty
        by_warehouse.append({"warehouseId": wid, "quantity": _money(qty), "skus": int(sku_cnt)})
    return {"skuCount": skus, "snapshotAt": latest_at.isoformat(),
            "totalQuantity": _money(total), "byWarehouse": by_warehouse}


def overview_metrics(db: Session) -> dict[str, Any]:
    """总览首屏 9 指标（规格 4）。全部来自本地库聚合，数据为空如实 None。"""
    paid_orders = (
        db.query(SalesOrder)
        .filter(SalesOrder.paid_amount.isnot(None))
        .all()
    )
    sales_amount = sum((to_decimal(o.paid_amount) for o in paid_orders), Decimal("0"))
    order_count = db.query(SalesOrder).count()

    refunds = db.query(AftersalesOrder).filter(AftersalesOrder.type == "refund").all()
    refund_amount = sum((to_decimal(r.refund_amount) for r in refunds), Decimal("0"))
    refund_rate = None
    if sales_amount > 0:
        refund_rate = f"{((refund_amount / sales_amount) * 100).quantize(Decimal('0.01'))}"

    from app.models.profit import ProfitSnapshot
    from app.services import reconciliation as rc

    recon = rc.overview(db)
    receivable = to_decimal(recon["receivable"])
    received = to_decimal(recon["received"])
    gross = db.query(ProfitSnapshot).filter(ProfitSnapshot.gross_profit.isnot(None)).order_by(ProfitSnapshot.id.desc()).first()

    return {
        "salesAmount": _money(sales_amount) if order_count else None,
        "netSales": _money(sales_amount) if order_count else None,
        "orderCount": order_count if order_count else None,
        "refundRate": refund_rate,
        "grossProfit": _money(gross.gross_profit) if gross else None,
        "receivable": _money(receivable) if receivable else None,
        "received": _money(received) if received else None,
        "pendingReceive": _money(receivable - received) if receivable else None,
    }


def list_orders(db: Session, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    q = db.query(SalesOrder).order_by(SalesOrder.ordered_at.desc().nullslast(), SalesOrder.id.desc())
    if status:
        q = q.filter(SalesOrder.order_status == status)
    rows = q.limit(limit).all()
    # Bulk fetch stores to avoid N+1 (was one db.get per order)
    store_ids = {o.store_id for o in rows if o.store_id}
    store_map: dict[int, Store] = {}
    if store_ids:
        store_map = {s.id: s for s in db.query(Store).filter(Store.id.in_(store_ids)).all()}
    out = []
    for o in rows:
        store = store_map.get(o.store_id) if o.store_id else None
        out.append({
            "id": o.id, "orderNo": o.order_no, "platform": o.platform,
            "storeName": store.name if store else "",
            "orderStatus": o.order_status, "payStatus": o.pay_status,
            "orderAmount": _money(o.order_amount), "paidAmount": _money(o.paid_amount),
            "orderedAt": o.ordered_at.isoformat() if o.ordered_at else None,
        })
    return out


def list_aftersales(db: Session, limit: int = 200) -> list[dict[str, Any]]:
    rows = db.query(AftersalesOrder).order_by(AftersalesOrder.created_at_src.desc().nullslast(),
                                              AftersalesOrder.id.desc()).limit(limit).all()
    return [
        {
            "id": r.id, "aftersaleNo": r.aftersale_no, "orderNo": r.order_no,
            "type": r.type, "status": r.status,
            "refundAmount": _money(r.refund_amount), "reason": r.reason,
            "createdAt": r.created_at_src.isoformat() if r.created_at_src else None,
        }
        for r in rows
    ]
