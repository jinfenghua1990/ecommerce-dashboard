from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.catalog import ProductSku
from app.models.purchase import ExternalPurchaseOrder, PurchaseAllocationItem
from app.services.procurement_chain_service import amount_close


def allocation_cost_anomalies(db: Session, po: ExternalPurchaseOrder) -> list[str]:
    """固定成本订单的单价核对；动态成本不产生价格异常。"""
    rows = db.query(PurchaseAllocationItem).filter_by(po_id=po.id).all()
    anomalies: list[str] = []
    for row in rows:
        if not row.sku_id or row.unit_price is None:
            continue
        sku = db.get(ProductSku, row.sku_id)
        if sku is None or sku.cost_mode == "dynamic" or sku.default_cost is None:
            continue
        tolerance = sku.cost_tolerance_pct if sku.cost_tolerance_pct is not None else Decimal("0.0200")
        if not amount_close(row.unit_price, sku.default_cost, tolerance):
            anomalies.append(f"{sku.sku_code}: 实际 {row.unit_price} vs 固定成本 {sku.default_cost}")
    return anomalies
