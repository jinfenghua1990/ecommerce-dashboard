"""经营看板（规格 6）：销售趋势 / 平台店铺排行 / SKU 排行 / 订单 / 售后 / 库存。

原则：
- 页面只查本地库（吉客云同步副本），不触发外部查询（规格 5）
- 数据未落地时如实返回空集，不伪造数字
- 全部金额 Decimal，输出 str
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.catalog import InventorySnapshot, Product, ProductSku, Store, Warehouse
from app.models.consumable import Consumable, ConsumableSkuMapping
from app.models.sales import AftersalesOrder, SalesOrder, SalesOrderItem
from app.utils.money import quantize, to_decimal


def _money(value: Decimal | None) -> str | None:
    return f"{quantize(to_decimal(value), Decimal('0.01')):f}" if value is not None else None


def _quantity(value: Decimal | None) -> str | None:
    return f"{to_decimal(value):f}" if value is not None else None


def _valid_sales():
    """业绩口径：剔除取消/作废/待审核订单（2026-09-07 与销售清单导入通道对齐）。"""
    o = SalesOrder
    return ~or_(
        o.order_status.like("已取消%"),
        o.order_status.like("作废%"),
        o.order_status == "待审核",
    )


def _range_conds(start: date | None, end: date | None) -> list[Any]:
    """ordered_at 闭区间过滤（含 end 当天；end 缺省不设上限），叠加业绩口径。"""
    conds = [_valid_sales()]
    if start is not None:
        conds.append(SalesOrder.ordered_at >= start)
    if end is not None:
        conds.append(SalesOrder.ordered_at < end + timedelta(days=1))
    return conds


def sales_trend(db: Session, days: int = 30, start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    """区间内每日销售额/订单数/退款率。按 ordered_at 分组（本地库）。
    start 传入时按 [start, end] 闭区间，否则取近 N 天。"""
    if start is not None:
        conds = _range_conds(start, end)
    else:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        conds = [SalesOrder.ordered_at >= since, _valid_sales()]
    rows = (
        db.query(
            func.date(SalesOrder.ordered_at),
            func.count(SalesOrder.id),
            func.coalesce(func.sum(SalesOrder.paid_amount), 0),
        )
        .filter(*conds)
        .group_by(func.date(SalesOrder.ordered_at))
        .order_by(func.date(SalesOrder.ordered_at))
        .all()
    )
    out = []
    for day, cnt, amt in rows:
        out.append({"date": day.isoformat(), "orders": int(cnt), "salesAmount": _money(amt)})
    return out


def platform_ranking(db: Session, start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    """平台/店铺销售排行。无平台字段的订单归入 unknown。"""
    rows = (
        db.query(SalesOrder.platform, func.count(SalesOrder.id), func.coalesce(func.sum(SalesOrder.paid_amount), 0))
        .filter(*_range_conds(start, end))
        .group_by(SalesOrder.platform)
        .order_by(func.sum(SalesOrder.paid_amount).desc())
        .all()
    )
    out = []
    for platform, cnt, amt in rows:
        out.append({"platform": platform or "unknown", "orders": int(cnt), "salesAmount": _money(amt)})
    return out


def sku_ranking(db: Session, limit: int = 20, start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    """SKU 销售额排行（来自订单明细本地副本）。"""
    rows = (
        db.query(
            SalesOrderItem.sku_code,
            SalesOrderItem.goods_name,
            func.count(SalesOrderItem.id),
            func.coalesce(func.sum(SalesOrderItem.amount), 0),
        )
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
        .filter(*_range_conds(start, end))
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
        by_warehouse.append({"warehouseId": wid, "quantity": _quantity(qty), "skus": int(sku_cnt)})
    return {"skuCount": skus, "snapshotAt": latest_at.isoformat(),
            "totalQuantity": _quantity(total), "byWarehouse": by_warehouse}


def inventory_skus(db: Session, search: str = "", limit: int = 1000) -> list[dict[str, Any]]:
    """SKU 级库存清单：每个货品在最新快照时点的库存总量与仓库分布。

    返回全部 SKU 档案（含无快照的），带 snapshot=false 标记；金额/数量输出 str。
    """
    snap = (
        db.query(InventorySnapshot)
        .order_by(InventorySnapshot.snapshot_at.desc())
        .first()
    )
    latest_at = snap.snapshot_at if snap else None
    rows = []
    if latest_at is not None:
        rows = (
            db.query(
                InventorySnapshot.sku_id,
                InventorySnapshot.warehouse_id,
                func.sum(InventorySnapshot.quantity),
            )
            .filter(InventorySnapshot.snapshot_at == latest_at)
            .group_by(InventorySnapshot.sku_id, InventorySnapshot.warehouse_id)
            .all()
        )
    agg: dict[int, dict[int | None, Decimal]] = {}
    for sku_id, wid, qty in rows:
        agg.setdefault(int(sku_id), {})[wid] = qty
    wh_names = {w.id: w.name for w in db.query(Warehouse).all()}

    q = (
        db.query(ProductSku, Product)
        .outerjoin(Product, Product.id == ProductSku.product_id)
        .order_by(ProductSku.sku_code, ProductSku.id)
    )
    term = search.strip()
    if term:
        pattern = f"%{term}%"
        q = q.filter(or_(
            ProductSku.sku_code.ilike(pattern),
            ProductSku.sku_name.ilike(pattern),
            ProductSku.barcode.ilike(pattern),
            Product.goods_name.ilike(pattern),
        ))

    out: list[dict[str, Any]] = []
    for sku, product in q.limit(limit).all():
        per = agg.get(sku.id, {})
        total = sum(per.values(), Decimal("0")) if per else Decimal("0")
        warehouses = [
            {"warehouseId": wid, "warehouseName": wh_names.get(wid) or "", "quantity": _quantity(qty)}
            for wid, qty in per.items()
        ]
        out.append({
            "skuId": sku.id,
            "jackyunSkuId": sku.jackyun_sku_id,
            "skuCode": sku.sku_code,
            "productType": sku.product_type or ("virtual_bundle" if sku.sku_code.upper().startswith("ES") else "single"),
            "skuName": sku.sku_name,
            "goodsName": product.goods_name if product else "",
            "barcode": sku.barcode,
            "unit": sku.unit,
            "status": sku.status,
            "quantity": _quantity(total),
            "hasSnapshot": bool(per),
            "warehouses": warehouses,
            "snapshotAt": latest_at.isoformat() if latest_at is not None else None,
        })
    return out


def list_products(db: Session, search: str = "", limit: int = 200) -> list[dict[str, Any]]:
    """吉客云商品/SKU 本地主档；供商品页和采购 SKU 选择器复用。"""
    q = (
        db.query(ProductSku, Product)
        .outerjoin(Product, Product.id == ProductSku.product_id)
        .order_by(ProductSku.sku_code, ProductSku.id)
    )
    term = search.strip()
    if term:
        pattern = f"%{term}%"
        q = q.filter(or_(
            ProductSku.sku_code.ilike(pattern),
            ProductSku.sku_name.ilike(pattern),
            ProductSku.barcode.ilike(pattern),
            Product.goods_name.ilike(pattern),
        ))
    return [
        {
            "id": sku.id,
            "jackyunSkuId": sku.jackyun_sku_id,
            "skuCode": sku.sku_code,
            "productType": sku.product_type or ("virtual_bundle" if sku.sku_code.upper().startswith("ES") else "single"),
            "skuName": sku.sku_name,
            "goodsName": product.goods_name if product else "",
            "barcode": sku.barcode,
            "unit": sku.unit,
            "salePrice": _money(sku.sale_price),
            "defaultCost": _money(sku.default_cost),
            "costMode": sku.cost_mode or "fixed",
            "costTolerancePct": str(sku.cost_tolerance_pct) if sku.cost_tolerance_pct is not None else "0.0200",
            "taxCode": sku.tax_code or "",
            "status": sku.status,
        }
        for sku, product in q.limit(limit).all()
    ]


def catalog_unified(db: Session, kind: str = "all", search: str = "", limit: int = 500) -> list[dict[str, Any]]:
    """统一货品档案：正品（吉客云 SKU）+ 耗材（本平台档案）合成一份列表。

    - kind=all/goods/consumable；search 命中编码/名称/条码。
    - 正品行库存取吉客云最新库存快照（只读吉客云，本系统不改动）；
    - 耗材行返回 自有仓/工厂/在途 三口径 + 安全库存预警 + 关联正品。
    - 条形码允许正品/耗材相同：系统内以 (kind, id) 独立 ID 区分，不以条码作主键。
    """
    term = search.strip()
    pattern = f"%{term}%" if term else None
    items: list[dict[str, Any]] = []

    if kind in {"all", "goods"}:
        # 正品库存：最新快照时点按 SKU 汇总
        snap = db.query(InventorySnapshot).order_by(InventorySnapshot.snapshot_at.desc()).first()
        latest_at = snap.snapshot_at if snap else None
        agg: dict[int, Decimal] = {}
        if latest_at is not None:
            rows = (
                db.query(InventorySnapshot.sku_id, func.sum(InventorySnapshot.quantity))
                .filter(InventorySnapshot.snapshot_at == latest_at)
                .group_by(InventorySnapshot.sku_id)
                .all()
            )
            agg = {int(sku_id): to_decimal(qty) for sku_id, qty in rows}
        q = (
            db.query(ProductSku, Product)
            .outerjoin(Product, Product.id == ProductSku.product_id)
            .order_by(ProductSku.sku_code, ProductSku.id)
        )
        if pattern:
            q = q.filter(or_(
                ProductSku.sku_code.ilike(pattern),
                ProductSku.sku_name.ilike(pattern),
                ProductSku.barcode.ilike(pattern),
                Product.goods_name.ilike(pattern),
                Product.category.ilike(pattern),
            ))
        for sku, product in q.limit(limit).all():
            items.append({
                "kind": "goods",
                "id": sku.id,
                "code": sku.sku_code,
                "name": sku.sku_name or (product.goods_name if product else ""),
                "goodsName": product.goods_name if product else "",
                "barcode": sku.barcode or "",
                "unit": sku.unit or "",
                "category": sku.product_type or "single",
                "goodsCategory": (product.category if product else "") or ((product.raw or {}).get("cateName", "") if product else "") or ((sku.raw or {}).get("cateName", "")),
                "status": sku.status,
                "stockOwn": _quantity(agg.get(sku.id)),
                "stockFactory": None,
                "stockTransit": None,
                "minStock": None,
                "lowStock": False,
                "hasSnapshot": sku.id in agg,
                "linkedSkus": [],
                "costMode": sku.cost_mode or "fixed",
                "costTolerancePct": str(sku.cost_tolerance_pct) if sku.cost_tolerance_pct is not None else "0.0200",
                "taxCode": sku.tax_code or "",
                "salePrice": _money(sku.sale_price),
                "defaultCost": _money(sku.default_cost),
            })

    if kind in {"all", "consumable"}:
        cq = db.query(Consumable).order_by(Consumable.code, Consumable.id)
        if pattern:
            cq = cq.filter(or_(
                Consumable.code.ilike(pattern),
                Consumable.name.ilike(pattern),
                Consumable.barcode.ilike(pattern),
            ))
        consumables = cq.limit(limit).all()
        link_rows = (
            db.query(ConsumableSkuMapping, ProductSku)
            .join(ProductSku, ProductSku.id == ConsumableSkuMapping.sku_id)
            .filter(ConsumableSkuMapping.consumable_id.in_([c.id for c in consumables]))
            .all()
        ) if consumables else []
        links: dict[int, list[dict[str, Any]]] = {}
        for mapping, sku in link_rows:
            links.setdefault(mapping.consumable_id, []).append(
                {"skuId": sku.id, "skuCode": sku.sku_code, "skuName": sku.sku_name}
            )
        for row in consumables:
            available = to_decimal(row.stock_qty) + to_decimal(row.factory_qty)
            min_qty = to_decimal(row.min_stock_qty)
            items.append({
                "kind": "consumable",
                "id": row.id,
                "code": row.code,
                "name": row.name,
                "goodsName": "",
                "barcode": row.barcode or "",
                "unit": row.unit,
                "category": row.category,
                "goodsCategory": row.category,
                "status": row.status,
                "stockOwn": _quantity(row.stock_qty),
                "stockFactory": _quantity(row.factory_qty),
                "stockTransit": _quantity(row.transit_qty),
                "minStock": _quantity(row.min_stock_qty),
                "lowStock": (min_qty > 0 and available <= min_qty) or to_decimal(row.stock_qty) < 0,
                "hasSnapshot": None,
                "linkedSkus": links.get(row.id, []),
                "costMode": None,
                "costTolerancePct": None,
                "taxCode": row.tax_code or "",
                "salePrice": None,
                "defaultCost": None,
            })

    items.sort(key=lambda r: (r["kind"], r["code"]))
    return items


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
    net_sales = sales_amount - refund_amount
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
        "netSales": _money(net_sales) if order_count else None,
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
