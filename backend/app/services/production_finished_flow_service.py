from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.models.production import (
    ProductionFinishedMovement,
    ProductionInboundAllocation,
    ProductionOrder,
    ProductionOrderItem,
)
from app.utils.money import to_decimal


FINISHED_MOVEMENT_TYPES = {"complete", "ship", "arrive"}
FINISHED_FLOW_ORDER_STATUSES = {
    "planned",
    "confirmed",
    "producing",
    "produced",
    "shipped",
    "arrived",
    "inbound",
}


def _qty(value: Decimal | None) -> str:
    return f"{to_decimal(value):f}"


def _movement_no(kind: str) -> str:
    prefix = {"complete": "FIN", "ship": "SHP", "arrive": "ARR"}[kind]
    return f"{prefix}-{datetime.now(timezone.utc):%Y%m%d}-{uuid4().hex[:6].upper()}"


def _normalize_item_quantities(items: list[dict]) -> dict[int, Decimal]:
    if not items:
        raise ValueError("至少需要一条成品明细")
    normalized: dict[int, Decimal] = {}
    for item in items:
        try:
            item_id = int(item.get("production_order_item_id"))
            quantity = to_decimal(item.get("quantity"))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("成品流转明细格式不正确") from exc
        if item_id <= 0 or not quantity.is_finite() or quantity <= 0:
            raise ValueError("成品流转数量必须大于 0")
        normalized[item_id] = normalized.get(item_id, Decimal("0")) + quantity
    return normalized


def _normalize_inbound_allocations(items: list[dict]) -> dict[tuple[int, int], Decimal]:
    if not items:
        raise ValueError("至少需要一条入库关联明细")
    normalized: dict[tuple[int, int], Decimal] = {}
    for item in items:
        try:
            production_item_id = int(item.get("production_order_item_id"))
            inbound_item_id = int(item.get("inbound_item_id"))
            quantity = to_decimal(item.get("quantity"))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("入库关联明细格式不正确") from exc
        if production_item_id <= 0 or inbound_item_id <= 0 or not quantity.is_finite() or quantity <= 0:
            raise ValueError("入库关联数量必须大于 0")
        key = (production_item_id, inbound_item_id)
        normalized[key] = normalized.get(key, Decimal("0")) + quantity
    return normalized


def _serialize_finished_movement(row: ProductionFinishedMovement, item: ProductionOrderItem | None = None) -> dict:
    return {
        "id": row.id,
        "movementNo": row.movement_no,
        "requestKey": row.request_key,
        "productionOrderId": row.production_order_id,
        "productionOrderItemId": row.production_order_item_id,
        "skuId": row.sku_id,
        "skuCode": item.sku_code if item else "",
        "skuName": item.sku_name if item else "",
        "unit": item.unit if item else "",
        "movementType": row.movement_type,
        "quantity": _qty(row.quantity),
        "carrier": row.carrier,
        "trackingNo": row.tracking_no,
        "note": row.note,
        "actor": row.actor,
        "occurredAt": row.occurred_at.isoformat() if row.occurred_at else None,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def _serialize_inbound_allocation(
    row: ProductionInboundAllocation,
    production_item: ProductionOrderItem | None = None,
    inbound_item: JackyunGoodsDocumentItem | None = None,
    document: JackyunGoodsDocument | None = None,
) -> dict:
    return {
        "id": row.id,
        "requestKey": row.request_key,
        "productionOrderId": row.production_order_id,
        "productionOrderItemId": row.production_order_item_id,
        "skuId": row.sku_id,
        "skuCode": production_item.sku_code if production_item else "",
        "skuName": production_item.sku_name if production_item else "",
        "quantity": _qty(row.quantity),
        "inboundDocumentId": row.inbound_document_id,
        "inboundDocumentNo": document.goodsdoc_no if document else "",
        "inboundDocumentAt": document.document_at.isoformat() if document and document.document_at else None,
        "warehouseName": document.warehouse_name if document else "",
        "inboundItemId": row.inbound_item_id,
        "inboundItemQuantity": _qty(inbound_item.quantity) if inbound_item and inbound_item.quantity is not None else None,
        "actor": row.actor,
        "linkedAt": row.linked_at.isoformat() if row.linked_at else None,
    }


def _item_flow(row: ProductionOrderItem) -> dict:
    planned = to_decimal(row.quantity)
    completed = to_decimal(row.completed_qty)
    shipped = to_decimal(row.shipped_qty)
    arrived = to_decimal(row.arrived_qty)
    inbound = to_decimal(row.inbound_qty)
    return {
        "id": row.id,
        "skuId": row.sku_id,
        "skuCode": row.sku_code,
        "skuName": row.sku_name,
        "unit": row.unit,
        "plannedQty": _qty(planned),
        "completedQty": _qty(completed),
        "remainingProductionQty": _qty(max(planned - completed, Decimal("0"))),
        "factoryReadyQty": _qty(max(completed - shipped, Decimal("0"))),
        "shippedQty": _qty(shipped),
        "transitQty": _qty(max(shipped - arrived, Decimal("0"))),
        "arrivedQty": _qty(arrived),
        "pendingInboundQty": _qty(max(arrived - inbound, Decimal("0"))),
        "inboundQty": _qty(inbound),
    }


def _refresh_order_status(db: Session, order: ProductionOrder) -> None:
    if order.status == "cancelled":
        return
    items = (
        db.query(ProductionOrderItem)
        .filter_by(production_order_id=order.id)
        .order_by(ProductionOrderItem.id)
        .all()
    )
    if not items:
        return
    if all(to_decimal(item.inbound_qty) >= to_decimal(item.quantity) for item in items):
        order.status = "completed"
    elif any(to_decimal(item.inbound_qty) > 0 for item in items):
        order.status = "inbound"
    elif any(to_decimal(item.arrived_qty) > 0 for item in items):
        order.status = "arrived"
    elif any(to_decimal(item.shipped_qty) > 0 for item in items):
        order.status = "shipped"
    elif all(to_decimal(item.completed_qty) >= to_decimal(item.quantity) for item in items):
        order.status = "produced"
    elif any(to_decimal(item.completed_qty) > 0 for item in items):
        order.status = "producing"
    elif order.status not in {"planned", "confirmed"}:
        order.status = "confirmed"


def production_finished_flow(db: Session, order_id: int) -> dict:
    order = db.get(ProductionOrder, order_id)
    if order is None:
        raise ValueError("生产单不存在")
    items = (
        db.query(ProductionOrderItem)
        .filter_by(production_order_id=order.id)
        .order_by(ProductionOrderItem.id)
        .all()
    )
    movements = list_finished_movements(db, order_id=order.id, limit=1000)
    inbound_links = list_inbound_allocations(db, order_id=order.id, limit=1000)
    return {
        "order": {
            "id": order.id,
            "orderNo": order.order_no,
            "factoryName": order.factory_name,
            "status": order.status,
            "expectedDeliveryDate": order.expected_delivery_date.isoformat() if order.expected_delivery_date else None,
        },
        "items": [_item_flow(item) for item in items],
        "movements": movements,
        "inboundAllocations": inbound_links,
    }


def list_finished_movements(
    db: Session,
    *,
    order_id: int | None = None,
    movement_type: str = "",
    limit: int = 500,
) -> list[dict]:
    query = db.query(ProductionFinishedMovement, ProductionOrderItem).join(
        ProductionOrderItem,
        ProductionOrderItem.id == ProductionFinishedMovement.production_order_item_id,
    )
    if order_id is not None:
        query = query.filter(ProductionFinishedMovement.production_order_id == order_id)
    if movement_type:
        if movement_type not in FINISHED_MOVEMENT_TYPES:
            raise ValueError("成品流转类型不正确")
        query = query.filter(ProductionFinishedMovement.movement_type == movement_type)
    rows = (
        query.order_by(
            ProductionFinishedMovement.occurred_at.desc(),
            ProductionFinishedMovement.id.desc(),
        )
        .limit(limit)
        .all()
    )
    return [_serialize_finished_movement(movement, item) for movement, item in rows]


def list_inbound_allocations(db: Session, *, order_id: int | None = None, limit: int = 500) -> list[dict]:
    query = (
        db.query(
            ProductionInboundAllocation,
            ProductionOrderItem,
            JackyunGoodsDocumentItem,
            JackyunGoodsDocument,
        )
        .join(ProductionOrderItem, ProductionOrderItem.id == ProductionInboundAllocation.production_order_item_id)
        .join(JackyunGoodsDocumentItem, JackyunGoodsDocumentItem.id == ProductionInboundAllocation.inbound_item_id)
        .join(JackyunGoodsDocument, JackyunGoodsDocument.id == ProductionInboundAllocation.inbound_document_id)
    )
    if order_id is not None:
        query = query.filter(ProductionInboundAllocation.production_order_id == order_id)
    rows = (
        query.order_by(
            ProductionInboundAllocation.linked_at.desc(),
            ProductionInboundAllocation.id.desc(),
        )
        .limit(limit)
        .all()
    )
    return [
        _serialize_inbound_allocation(allocation, production_item, inbound_item, document)
        for allocation, production_item, inbound_item, document in rows
    ]


def _existing_finished_request(
    db: Session,
    *,
    order_id: int,
    movement_type: str,
    request_key: str,
    requested: dict[int, Decimal],
) -> list[ProductionFinishedMovement] | None:
    existing = (
        db.query(ProductionFinishedMovement)
        .filter_by(
            production_order_id=order_id,
            movement_type=movement_type,
            request_key=request_key,
        )
        .order_by(ProductionFinishedMovement.production_order_item_id)
        .all()
    )
    if not existing:
        return None
    actual = {row.production_order_item_id: to_decimal(row.quantity) for row in existing}
    if actual != requested:
        raise ValueError("该请求编号已经提交过不同的成品数量，请刷新页面后重试")
    return existing


def record_finished_movement(
    db: Session,
    *,
    order_id: int,
    movement_type: str,
    items: list[dict],
    actor: str,
    request_key: str,
    carrier: str = "",
    tracking_no: str = "",
    note: str = "",
) -> list[dict]:
    if movement_type not in FINISHED_MOVEMENT_TYPES:
        raise ValueError("成品流转类型不正确")
    requested = _normalize_item_quantities(items)
    key = request_key.strip()
    if not key:
        raise ValueError("缺少请求编号，请刷新页面后重试")

    existing = _existing_finished_request(
        db,
        order_id=order_id,
        movement_type=movement_type,
        request_key=key,
        requested=requested,
    )
    if existing is not None:
        item_map = {
            row.id: row
            for row in db.query(ProductionOrderItem)
            .filter(ProductionOrderItem.id.in_([movement.production_order_item_id for movement in existing]))
            .all()
        }
        return [_serialize_finished_movement(row, item_map.get(row.production_order_item_id)) for row in existing]

    order = db.scalar(select(ProductionOrder).where(ProductionOrder.id == order_id).with_for_update())
    if order is None:
        raise ValueError("生产单不存在")
    if order.status not in FINISHED_FLOW_ORDER_STATUSES:
        raise ValueError("当前生产单状态不能登记成品流转")

    item_ids = sorted(requested)
    locked_items = db.scalars(
        select(ProductionOrderItem)
        .where(
            ProductionOrderItem.id.in_(item_ids),
            ProductionOrderItem.production_order_id == order_id,
        )
        .order_by(ProductionOrderItem.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    item_map = {row.id: row for row in locked_items}
    missing = [item_id for item_id in item_ids if item_id not in item_map]
    if missing:
        raise ValueError(f"生产成品明细不存在：{', '.join(str(value) for value in missing)}")

    for item_id in item_ids:
        row = item_map[item_id]
        quantity = requested[item_id]
        if movement_type == "complete":
            available = to_decimal(row.quantity) - to_decimal(row.completed_qty)
            label = "待生产"
        elif movement_type == "ship":
            available = to_decimal(row.completed_qty) - to_decimal(row.shipped_qty)
            label = "工厂待发"
        else:
            available = to_decimal(row.shipped_qty) - to_decimal(row.arrived_qty)
            label = "成品在途"
        if quantity > available:
            raise ValueError(f"{row.sku_code} 本次数量 {quantity} 超过{label}数量 {available}")

    movement_no = _movement_no(movement_type)
    now = datetime.now(timezone.utc)
    movements: list[ProductionFinishedMovement] = []
    for item_id in item_ids:
        row = item_map[item_id]
        quantity = requested[item_id]
        movement = ProductionFinishedMovement(
            movement_no=movement_no,
            request_key=key,
            production_order_id=order.id,
            production_order_item_id=row.id,
            sku_id=row.sku_id,
            movement_type=movement_type,
            quantity=quantity,
            carrier=carrier.strip() if movement_type == "ship" else "",
            tracking_no=tracking_no.strip() if movement_type == "ship" else "",
            note=note.strip(),
            actor=actor,
            occurred_at=now,
        )
        db.add(movement)
        if movement_type == "complete":
            row.completed_qty = to_decimal(row.completed_qty) + quantity
        elif movement_type == "ship":
            row.shipped_qty = to_decimal(row.shipped_qty) + quantity
        else:
            row.arrived_qty = to_decimal(row.arrived_qty) + quantity
        movements.append(movement)

    db.flush()
    _refresh_order_status(db, order)
    db.commit()
    return [_serialize_finished_movement(row, item_map.get(row.production_order_item_id)) for row in movements]


def inbound_candidates(db: Session, order_id: int, *, limit: int = 300) -> dict:
    order = db.get(ProductionOrder, order_id)
    if order is None:
        raise ValueError("生产单不存在")
    production_items = (
        db.query(ProductionOrderItem)
        .filter_by(production_order_id=order.id)
        .order_by(ProductionOrderItem.id)
        .all()
    )
    pending = {
        item.sku_id: max(to_decimal(item.arrived_qty) - to_decimal(item.inbound_qty), Decimal("0"))
        for item in production_items
    }
    sku_ids = [sku_id for sku_id, quantity in pending.items() if quantity > 0]
    if not sku_ids:
        return {"orderId": order.id, "rows": []}

    inbound_rows = (
        db.query(JackyunGoodsDocumentItem, JackyunGoodsDocument)
        .join(JackyunGoodsDocument, JackyunGoodsDocument.id == JackyunGoodsDocumentItem.document_id)
        .filter(
            JackyunGoodsDocument.document_type == "inbound",
            JackyunGoodsDocumentItem.matched_sku_id.in_(sku_ids),
            JackyunGoodsDocumentItem.quantity.isnot(None),
        )
        .order_by(
            JackyunGoodsDocument.document_at.desc().nullslast(),
            JackyunGoodsDocument.id.desc(),
            JackyunGoodsDocumentItem.line_no,
        )
        .limit(limit)
        .all()
    )
    inbound_item_ids = [item.id for item, _ in inbound_rows]
    allocated_rows = (
        db.query(
            ProductionInboundAllocation.inbound_item_id,
            func.coalesce(func.sum(ProductionInboundAllocation.quantity), 0),
        )
        .filter(ProductionInboundAllocation.inbound_item_id.in_(inbound_item_ids))
        .group_by(ProductionInboundAllocation.inbound_item_id)
        .all()
        if inbound_item_ids
        else []
    )
    allocated = {int(item_id): to_decimal(quantity) for item_id, quantity in allocated_rows}
    production_by_sku = {item.sku_id: item for item in production_items}

    rows: list[dict] = []
    for inbound_item, document in inbound_rows:
        inbound_quantity = to_decimal(inbound_item.quantity)
        available = max(inbound_quantity - allocated.get(inbound_item.id, Decimal("0")), Decimal("0"))
        production_item = production_by_sku.get(inbound_item.matched_sku_id)
        if production_item is None or available <= 0:
            continue
        production_pending = max(
            to_decimal(production_item.arrived_qty) - to_decimal(production_item.inbound_qty),
            Decimal("0"),
        )
        if production_pending <= 0:
            continue
        rows.append({
            "productionOrderItemId": production_item.id,
            "skuId": production_item.sku_id,
            "skuCode": production_item.sku_code,
            "skuName": production_item.sku_name,
            "productionPendingInboundQty": _qty(production_pending),
            "inboundDocumentId": document.id,
            "inboundDocumentNo": document.goodsdoc_no,
            "inboundDocumentAt": document.document_at.isoformat() if document.document_at else None,
            "warehouseName": document.warehouse_name,
            "supplierName": document.supplier_name,
            "inboundItemId": inbound_item.id,
            "inboundItemQuantity": _qty(inbound_quantity),
            "inboundItemAvailableQty": _qty(available),
            "suggestedLinkQty": _qty(min(production_pending, available)),
        })
    return {"orderId": order.id, "rows": rows}


def link_inbound_allocations(
    db: Session,
    *,
    order_id: int,
    items: list[dict],
    actor: str,
    request_key: str,
) -> list[dict]:
    requested = _normalize_inbound_allocations(items)
    key = request_key.strip()
    if not key:
        raise ValueError("缺少请求编号，请刷新页面后重试")

    existing = (
        db.query(ProductionInboundAllocation)
        .filter_by(production_order_id=order_id, request_key=key)
        .order_by(ProductionInboundAllocation.production_order_item_id, ProductionInboundAllocation.inbound_item_id)
        .all()
    )
    if existing:
        actual = {
            (row.production_order_item_id, row.inbound_item_id): to_decimal(row.quantity)
            for row in existing
        }
        if actual != requested:
            raise ValueError("该请求编号已经关联过不同的入库数量，请刷新页面后重试")
        return list_inbound_allocations_for_rows(db, existing)

    order = db.scalar(select(ProductionOrder).where(ProductionOrder.id == order_id).with_for_update())
    if order is None:
        raise ValueError("生产单不存在")
    if order.status == "cancelled":
        raise ValueError("已取消生产单不能关联入库")

    production_item_ids = sorted({item_id for item_id, _ in requested})
    production_items = db.scalars(
        select(ProductionOrderItem)
        .where(
            ProductionOrderItem.id.in_(production_item_ids),
            ProductionOrderItem.production_order_id == order_id,
        )
        .order_by(ProductionOrderItem.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    production_map = {row.id: row for row in production_items}
    missing_production = [item_id for item_id in production_item_ids if item_id not in production_map]
    if missing_production:
        raise ValueError(f"生产成品明细不存在：{', '.join(str(value) for value in missing_production)}")

    inbound_item_ids = sorted({inbound_item_id for _, inbound_item_id in requested})
    inbound_items = db.scalars(
        select(JackyunGoodsDocumentItem)
        .where(JackyunGoodsDocumentItem.id.in_(inbound_item_ids))
        .order_by(JackyunGoodsDocumentItem.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    inbound_map = {row.id: row for row in inbound_items}
    missing_inbound = [item_id for item_id in inbound_item_ids if item_id not in inbound_map]
    if missing_inbound:
        raise ValueError(f"吉客云入库明细不存在：{', '.join(str(value) for value in missing_inbound)}")

    document_ids = sorted({row.document_id for row in inbound_items})
    documents = db.scalars(
        select(JackyunGoodsDocument)
        .where(JackyunGoodsDocument.id.in_(document_ids))
        .order_by(JackyunGoodsDocument.id)
    ).all()
    document_map = {row.id: row for row in documents}
    for document_id in document_ids:
        document = document_map.get(document_id)
        if document is None or document.document_type != "inbound":
            raise ValueError("只能关联吉客云真实入库单")

    already_allocated_rows = (
        db.query(
            ProductionInboundAllocation.inbound_item_id,
            func.coalesce(func.sum(ProductionInboundAllocation.quantity), 0),
        )
        .filter(ProductionInboundAllocation.inbound_item_id.in_(inbound_item_ids))
        .group_by(ProductionInboundAllocation.inbound_item_id)
        .all()
    )
    already_allocated = {
        int(inbound_item_id): to_decimal(quantity)
        for inbound_item_id, quantity in already_allocated_rows
    }
    request_by_inbound: dict[int, Decimal] = {}
    request_by_production: dict[int, Decimal] = {}

    for (production_item_id, inbound_item_id), quantity in requested.items():
        production_item = production_map[production_item_id]
        inbound_item = inbound_map[inbound_item_id]
        if inbound_item.matched_sku_id != production_item.sku_id:
            raise ValueError(f"吉客云入库明细 #{inbound_item.id} 与 {production_item.sku_code} 不是同一 SKU")
        request_by_inbound[inbound_item_id] = request_by_inbound.get(inbound_item_id, Decimal("0")) + quantity
        request_by_production[production_item_id] = request_by_production.get(production_item_id, Decimal("0")) + quantity

    for production_item_id, quantity in request_by_production.items():
        production_item = production_map[production_item_id]
        pending = to_decimal(production_item.arrived_qty) - to_decimal(production_item.inbound_qty)
        if quantity > pending:
            raise ValueError(
                f"{production_item.sku_code} 本次关联 {quantity} 超过已到货待入库 {pending}"
            )

    for inbound_item_id, quantity in request_by_inbound.items():
        inbound_item = inbound_map[inbound_item_id]
        total = to_decimal(inbound_item.quantity)
        available = total - already_allocated.get(inbound_item_id, Decimal("0"))
        if quantity > available:
            raise ValueError(
                f"吉客云入库明细 #{inbound_item_id} 本次关联 {quantity} 超过尚未分配数量 {available}"
            )

    now = datetime.now(timezone.utc)
    rows: list[ProductionInboundAllocation] = []
    for (production_item_id, inbound_item_id), quantity in requested.items():
        production_item = production_map[production_item_id]
        inbound_item = inbound_map[inbound_item_id]
        row = ProductionInboundAllocation(
            request_key=key,
            production_order_id=order.id,
            production_order_item_id=production_item.id,
            sku_id=production_item.sku_id,
            inbound_document_id=inbound_item.document_id,
            inbound_item_id=inbound_item.id,
            quantity=quantity,
            actor=actor,
            linked_at=now,
        )
        db.add(row)
        production_item.inbound_qty = to_decimal(production_item.inbound_qty) + quantity
        rows.append(row)

    db.flush()
    _refresh_order_status(db, order)
    db.commit()
    return list_inbound_allocations_for_rows(db, rows)


def list_inbound_allocations_for_rows(db: Session, rows: list[ProductionInboundAllocation]) -> list[dict]:
    if not rows:
        return []
    production_ids = {row.production_order_item_id for row in rows}
    inbound_ids = {row.inbound_item_id for row in rows}
    document_ids = {row.inbound_document_id for row in rows}
    production_map = {
        row.id: row for row in db.query(ProductionOrderItem).filter(ProductionOrderItem.id.in_(production_ids)).all()
    }
    inbound_map = {
        row.id: row for row in db.query(JackyunGoodsDocumentItem).filter(JackyunGoodsDocumentItem.id.in_(inbound_ids)).all()
    }
    document_map = {
        row.id: row for row in db.query(JackyunGoodsDocument).filter(JackyunGoodsDocument.id.in_(document_ids)).all()
    }
    return [
        _serialize_inbound_allocation(
            row,
            production_map.get(row.production_order_item_id),
            inbound_map.get(row.inbound_item_id),
            document_map.get(row.inbound_document_id),
        )
        for row in rows
    ]
