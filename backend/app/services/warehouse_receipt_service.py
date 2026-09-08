from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.consumable import ConsumableTransaction
from app.models.consumable_purchase import ConsumablePurchase, ConsumableReceipt
from app.services import consumable_purchase_service as purchase_svc
from app.services import warehouse_service


def receive_consumable_purchase(
    db: Session,
    purchase_id: int,
    *,
    request_key: str,
    received_on: date,
    items: list[dict],
    warehouse_id: int | None = None,
    note: str = "",
    actor: str = "",
) -> ConsumablePurchase:
    """按仓库主档登记耗材收货，同时兼容旧 own/factory 库存口径。

    新操作必须落 warehouse_id；如果前端未传（旧客户端），默认使用第一个启用的工厂仓。
    """
    warehouse = (
        warehouse_service.get_active(db, warehouse_id)
        if warehouse_id is not None
        else warehouse_service.default_for_type(db, "factory")
    )
    if warehouse is None:
        raise ValueError("没有可用的工厂仓，请先到设置 → 仓库配置中新建或启用仓库")
    if warehouse.purpose not in {"consumable", "both"}:
        raise ValueError(f"仓库“{warehouse.name}”当前用途不允许存放耗材")

    existing = db.query(ConsumableReceipt).filter_by(request_key=request_key).first()
    if existing is not None and existing.warehouse_id not in (None, warehouse.id):
        raise ValueError("这次收货已提交到其他仓库，请刷新后重新操作")

    legacy_location = warehouse_service.legacy_location(warehouse)
    row = purchase_svc.receive_purchase(
        db,
        purchase_id,
        request_key=request_key,
        received_on=received_on,
        items=items,
        note=note,
        actor=actor,
        location=legacy_location,
    )

    receipt = db.query(ConsumableReceipt).filter_by(request_key=request_key).first()
    if receipt is None:
        raise ValueError("收货记录保存失败")
    receipt.warehouse_id = warehouse.id
    db.query(ConsumableTransaction).filter(
        ConsumableTransaction.source_type == "consumable_receipt",
        ConsumableTransaction.source_id == receipt.id,
    ).update({"warehouse_id": warehouse.id}, synchronize_session=False)
    db.commit()
    return row
