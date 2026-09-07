from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.catalog import InventorySnapshot, Product, ProductSku
from app.models.purchase import ExternalPurchaseOrder, PurchaseAllocationItem
from app.models.sales import SalesOrder, SalesOrderItem
from app.utils.money import to_decimal

router = APIRouter(prefix="/supply-chain", tags=["supply-chain"])

OPEN_SUPPLY_STATUSES = {
    "confirmed",
    "jackyun_linked",
    "producing",
    "shipped",
    "arrived",
}


def _qty(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{to_decimal(value):f}"


def _days(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value.quantize(Decimal('0.1')):f}"


def _valid_sales_condition():
    return ~or_(
        SalesOrder.order_status.like("已取消%"),
        SalesOrder.order_status.like("作废%"),
        SalesOrder.order_status == "待审核",
    )


@router.get("/replenishment")
def replenishment(
    days: int = Query(30, ge=7, le=180, description="销量观察周期"),
    lead_days: int = Query(14, ge=1, le=120, description="补货/生产交期天数"),
    safety_days: int = Query(7, ge=0, le=90, description="安全库存覆盖天数"),
    search: str = Query("", max_length=100),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """真实数据驱动的补货建议。

    数据来源：
    - 当前库存：吉客云最新库存快照；
    - 近销：本地销售订单明细 quantity，剔除取消/作废/待审核订单；
    - 待供应：已确认且尚未入库完成的采购分配数量。

    建议补货 = max(日均销量 × (交期天数 + 安全天数) - 当前库存 - 待供应, 0)。
    无库存快照或观察期无销量时不伪造建议数量，返回 null 并给出原因。
    """
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    latest_at = db.query(func.max(InventorySnapshot.snapshot_at)).scalar()
    inventory_by_sku: dict[int, Decimal] = {}
    if latest_at is not None:
        inventory_rows = (
            db.query(InventorySnapshot.sku_id, func.coalesce(func.sum(InventorySnapshot.quantity), 0))
            .filter(InventorySnapshot.snapshot_at == latest_at)
            .group_by(InventorySnapshot.sku_id)
            .all()
        )
        inventory_by_sku = {int(sku_id): to_decimal(quantity) for sku_id, quantity in inventory_rows}

    sales_rows = (
        db.query(
            SalesOrderItem.sku_id,
            SalesOrderItem.sku_code,
            func.coalesce(func.sum(SalesOrderItem.quantity), 0),
        )
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.order_id)
        .filter(SalesOrder.ordered_at >= since, _valid_sales_condition())
        .group_by(SalesOrderItem.sku_id, SalesOrderItem.sku_code)
        .all()
    )
    sales_by_id: dict[int, Decimal] = {}
    sales_by_code: dict[str, Decimal] = {}
    for sku_id, sku_code, quantity in sales_rows:
        qty = to_decimal(quantity)
        if sku_id is not None:
            sales_by_id[int(sku_id)] = sales_by_id.get(int(sku_id), Decimal("0")) + qty
        elif sku_code:
            sales_by_code[sku_code] = sales_by_code.get(sku_code, Decimal("0")) + qty

    open_rows = (
        db.query(
            PurchaseAllocationItem.sku_id,
            PurchaseAllocationItem.sku_code,
            func.coalesce(func.sum(PurchaseAllocationItem.quantity), 0),
        )
        .join(ExternalPurchaseOrder, ExternalPurchaseOrder.id == PurchaseAllocationItem.po_id)
        .filter(ExternalPurchaseOrder.purchase_status.in_(OPEN_SUPPLY_STATUSES))
        .group_by(PurchaseAllocationItem.sku_id, PurchaseAllocationItem.sku_code)
        .all()
    )
    open_by_id: dict[int, Decimal] = {}
    open_by_code: dict[str, Decimal] = {}
    for sku_id, sku_code, quantity in open_rows:
        qty = to_decimal(quantity)
        if sku_id is not None:
            open_by_id[int(sku_id)] = open_by_id.get(int(sku_id), Decimal("0")) + qty
        elif sku_code:
            open_by_code[sku_code] = open_by_code.get(sku_code, Decimal("0")) + qty

    query = (
        db.query(ProductSku, Product)
        .outerjoin(Product, Product.id == ProductSku.product_id)
        .filter(ProductSku.status == "active")
        .order_by(ProductSku.sku_code, ProductSku.id)
    )
    term = search.strip()
    if term:
        pattern = f"%{term}%"
        query = query.filter(or_(
            ProductSku.sku_code.ilike(pattern),
            ProductSku.sku_name.ilike(pattern),
            ProductSku.barcode.ilike(pattern),
            Product.goods_name.ilike(pattern),
        ))

    rows: list[dict[str, Any]] = []
    urgent_count = 0
    attention_count = 0
    suggested_count = 0

    for sku, product in query.limit(limit).all():
        sold = sales_by_id.get(sku.id, sales_by_code.get(sku.sku_code, Decimal("0")))
        open_supply = open_by_id.get(sku.id, open_by_code.get(sku.sku_code, Decimal("0")))
        current = inventory_by_sku.get(sku.id)
        avg_daily = sold / Decimal(days) if sold > 0 else Decimal("0")

        target_stock: Decimal | None = None
        suggested: Decimal | None = None
        current_cover: Decimal | None = None
        effective_cover: Decimal | None = None
        stockout_date: str | None = None

        if current is None:
            risk = "no_snapshot"
            reason = "该 SKU 没有最新库存快照，暂不计算补货数量"
        elif avg_daily <= 0:
            risk = "no_sales"
            reason = f"近 {days} 天没有有效销量，暂不自动给出补货数量"
        else:
            target_stock = avg_daily * Decimal(lead_days + safety_days)
            effective_stock = current + open_supply
            suggested = max(target_stock - effective_stock, Decimal("0"))
            current_cover = current / avg_daily
            effective_cover = effective_stock / avg_daily
            if current_cover >= 0:
                stockout_date = (now + timedelta(days=float(current_cover))).date().isoformat()

            if current_cover < Decimal(lead_days):
                risk = "urgent"
                reason = "当前库存覆盖天数低于补货/生产交期"
                urgent_count += 1
            elif effective_cover < Decimal(lead_days + safety_days):
                risk = "attention"
                reason = "当前库存加待供应仍低于目标覆盖天数"
                attention_count += 1
            else:
                risk = "ok"
                reason = "当前库存与待供应可覆盖目标周期"
            if suggested > 0:
                suggested_count += 1

        rows.append({
            "skuId": sku.id,
            "skuCode": sku.sku_code,
            "skuName": sku.sku_name or "",
            "goodsName": product.goods_name if product else "",
            "barcode": sku.barcode or "",
            "unit": sku.unit or "",
            "currentInventory": _qty(current),
            "soldQuantity": _qty(sold),
            "averageDailySales": _qty(avg_daily),
            "openSupplyQuantity": _qty(open_supply),
            "targetStock": _qty(target_stock),
            "suggestedReplenishment": _qty(suggested),
            "currentCoverDays": _days(current_cover),
            "effectiveCoverDays": _days(effective_cover),
            "estimatedStockoutDate": stockout_date,
            "risk": risk,
            "reason": reason,
        })

    return {
        "policy": {
            "salesWindowDays": days,
            "leadDays": lead_days,
            "safetyDays": safety_days,
            "targetCoverDays": lead_days + safety_days,
            "formula": "日均销量 × (交期天数 + 安全天数) - 当前库存 - 待供应",
        },
        "data": {
            "inventorySnapshotAt": latest_at.isoformat() if latest_at is not None else None,
            "salesSince": since.isoformat(),
            "generatedAt": now.isoformat(),
        },
        "summary": {
            "skuCount": len(rows),
            "urgentCount": urgent_count,
            "attentionCount": attention_count,
            "suggestedCount": suggested_count,
        },
        "rows": rows,
    }
