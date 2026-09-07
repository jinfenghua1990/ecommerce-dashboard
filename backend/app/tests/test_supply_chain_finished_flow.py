from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.catalog import InventorySnapshot, ProductSku
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.services.production_finished_flow_service import (
    inbound_candidates,
    link_inbound_allocations,
    production_finished_flow,
    record_finished_movement,
)
from app.services.production_service import create_production_order, production_order_detail


def _sku(db_session) -> ProductSku:
    row = ProductSku(
        jackyun_sku_id=f"pytest-finished-{uuid4().hex}",
        sku_code=f"FIN-SKU-{uuid4().hex[:8]}",
        sku_name="成品流转测试 SKU",
        unit="盒",
        status="active",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _order(db_session, quantity: str = "80"):
    sku = _sku(db_session)
    order = create_production_order(
        db_session,
        factory_name="成品流转测试工厂",
        items=[{"sku_id": sku.id, "quantity": Decimal(quantity)}],
        actor="pytest",
    )
    item_id = production_order_detail(db_session, order)["items"][0]["id"]
    return order, sku, item_id


def _inbound_item(db_session, sku: ProductSku, quantity: str, suffix: str):
    document = JackyunGoodsDocument(
        document_type="inbound",
        goodsdoc_no=f"JKY-IN-{suffix}-{uuid4().hex[:6]}",
        document_at=datetime.now(timezone.utc),
        warehouse_code="WH-TEST",
        warehouse_name="测试仓",
        supplier_name="成品流转测试工厂",
        total_quantity=Decimal(quantity),
    )
    db_session.add(document)
    db_session.flush()
    item = JackyunGoodsDocumentItem(
        document_id=document.id,
        line_no=1,
        goods_no=sku.sku_code,
        sku_barcode=sku.barcode or "",
        goods_name=sku.sku_name,
        quantity=Decimal(quantity),
        unit_name=sku.unit,
        matched_sku_id=sku.id,
        match_status="auto",
    )
    db_session.add(item)
    db_session.flush()
    return document, item


def test_finished_goods_partial_flow_and_idempotency(db_session):
    order, _, item_id = _order(db_session)

    complete_key = str(uuid4())
    record_finished_movement(
        db_session,
        order_id=order.id,
        movement_type="complete",
        items=[{"production_order_item_id": item_id, "quantity": "60"}],
        actor="pytest",
        request_key=complete_key,
    )
    # 重复请求必须幂等，不能再次累计已生产。
    record_finished_movement(
        db_session,
        order_id=order.id,
        movement_type="complete",
        items=[{"production_order_item_id": item_id, "quantity": "60"}],
        actor="pytest",
        request_key=complete_key,
    )
    record_finished_movement(
        db_session,
        order_id=order.id,
        movement_type="ship",
        items=[{"production_order_item_id": item_id, "quantity": "40"}],
        actor="pytest",
        request_key=str(uuid4()),
        carrier="测试物流",
        tracking_no="FIN-TRACK-001",
    )
    record_finished_movement(
        db_session,
        order_id=order.id,
        movement_type="arrive",
        items=[{"production_order_item_id": item_id, "quantity": "25"}],
        actor="pytest",
        request_key=str(uuid4()),
    )

    flow = production_finished_flow(db_session, order.id)
    item = flow["items"][0]
    assert item["plannedQty"] == "80.0000"
    assert item["completedQty"] == "60.0000"
    assert item["remainingProductionQty"] == "20.0000"
    assert item["factoryReadyQty"] == "20.0000"
    assert item["shippedQty"] == "40.0000"
    assert item["transitQty"] == "15.0000"
    assert item["arrivedQty"] == "25.0000"
    assert item["pendingInboundQty"] == "25.0000"
    assert item["inboundQty"] == "0.0000"

    with pytest.raises(ValueError, match="超过工厂待发数量"):
        record_finished_movement(
            db_session,
            order_id=order.id,
            movement_type="ship",
            items=[{"production_order_item_id": item_id, "quantity": "21"}],
            actor="pytest",
            request_key=str(uuid4()),
        )


def test_inbound_allocation_can_be_partial_without_changing_inventory_snapshot(db_session):
    order, sku, item_id = _order(db_session, "30")
    snapshot = InventorySnapshot(
        sku_id=sku.id,
        warehouse_id=None,
        quantity=Decimal("99"),
        snapshot_at=datetime.now(timezone.utc),
        source="jackyun",
    )
    db_session.add(snapshot)
    db_session.flush()

    for movement_type, quantity in (("complete", "30"), ("ship", "30"), ("arrive", "30")):
        record_finished_movement(
            db_session,
            order_id=order.id,
            movement_type=movement_type,
            items=[{"production_order_item_id": item_id, "quantity": quantity}],
            actor="pytest",
            request_key=str(uuid4()),
        )

    _, inbound_item = _inbound_item(db_session, sku, "20", "A")
    candidates = inbound_candidates(db_session, order.id)
    candidate = next(row for row in candidates["rows"] if row["inboundItemId"] == inbound_item.id)
    assert candidate["productionPendingInboundQty"] == "30.0000"
    assert candidate["inboundItemAvailableQty"] == "20.0000"
    assert candidate["suggestedLinkQty"] == "20.0000"

    first_key = str(uuid4())
    link_inbound_allocations(
        db_session,
        order_id=order.id,
        items=[{
            "production_order_item_id": item_id,
            "inbound_item_id": inbound_item.id,
            "quantity": "12",
        }],
        actor="pytest",
        request_key=first_key,
    )
    # 同一个 request_key 重放不重复认领。
    link_inbound_allocations(
        db_session,
        order_id=order.id,
        items=[{
            "production_order_item_id": item_id,
            "inbound_item_id": inbound_item.id,
            "quantity": "12",
        }],
        actor="pytest",
        request_key=first_key,
    )

    # 同一吉客云明细允许第二次分批关联剩余数量。
    link_inbound_allocations(
        db_session,
        order_id=order.id,
        items=[{
            "production_order_item_id": item_id,
            "inbound_item_id": inbound_item.id,
            "quantity": "8",
        }],
        actor="pytest",
        request_key=str(uuid4()),
    )

    with pytest.raises(ValueError, match="超过尚未分配数量"):
        link_inbound_allocations(
            db_session,
            order_id=order.id,
            items=[{
                "production_order_item_id": item_id,
                "inbound_item_id": inbound_item.id,
                "quantity": "1",
            }],
            actor="pytest",
            request_key=str(uuid4()),
        )

    db_session.refresh(snapshot)
    assert snapshot.quantity == Decimal("99")
    flow = production_finished_flow(db_session, order.id)
    assert flow["items"][0]["inboundQty"] == "20.0000"
    assert flow["items"][0]["pendingInboundQty"] == "10.0000"
