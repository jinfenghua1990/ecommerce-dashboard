from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.models.consumable import Consumable, ConsumableTransaction
from app.models.consumable_purchase import ConsumableReceipt
from app.services import consumable_purchase_service as purchase_svc
from app.services import warehouse_purchase_view
from app.services import warehouse_receipt_service
from app.services import warehouse_service


def test_warehouse_can_be_renamed_without_changing_identity(db_session):
    code = f"FACTORY-{uuid4().hex[:8].upper()}"
    row = warehouse_service.create_warehouse(
        db_session,
        code=code,
        name="测试工厂仓",
        warehouse_type="factory",
        purpose="both",
        is_sellable=False,
    )
    warehouse_id = row.id

    updated = warehouse_service.update_warehouse(
        db_session,
        warehouse_id,
        name="测试工厂仓（新名称）",
    )

    assert updated.id == warehouse_id
    assert updated.name == "测试工厂仓（新名称）"
    assert updated.code == code


def test_consumable_receipt_uses_selected_factory_warehouse(db_session):
    warehouse = warehouse_service.create_warehouse(
        db_session,
        code=f"FACTORY-{uuid4().hex[:8].upper()}",
        name="指定工厂仓",
        warehouse_type="factory",
        purpose="both",
        is_sellable=False,
    )
    material = Consumable(
        code=f"HC-{uuid4().hex[:8].upper()}",
        name="测试彩盒",
        unit="个",
        stock_qty=Decimal("0"),
        factory_qty=Decimal("0"),
        transit_qty=Decimal("0"),
        purchased_qty=Decimal("0"),
        used_qty=Decimal("0"),
        min_stock_qty=Decimal("0"),
        status="active",
        raw={},
    )
    db_session.add(material)
    db_session.flush()

    purchase = purchase_svc.create_purchase(
        db_session,
        request_key=str(uuid4()),
        supplier_name="测试包材供应商",
        ordered_on=date(2026, 9, 8),
        items=[{
            "consumable_id": material.id,
            "quantity": Decimal("100"),
            "unit_cost": Decimal("0.5"),
        }],
        actor="pytest",
    )
    line = purchase_svc.serialize_purchase(db_session, purchase, detail=True)["items"][0]
    receipt_key = str(uuid4())

    warehouse_receipt_service.receive_consumable_purchase(
        db_session,
        purchase.id,
        request_key=receipt_key,
        received_on=date(2026, 9, 9),
        warehouse_id=warehouse.id,
        items=[{"item_id": line["id"], "quantity": Decimal("40")}],
        actor="pytest",
    )

    db_session.refresh(material)
    receipt = db_session.query(ConsumableReceipt).filter_by(request_key=receipt_key).one()
    tx = db_session.query(ConsumableTransaction).filter_by(
        source_type="consumable_receipt",
        source_id=receipt.id,
        consumable_id=material.id,
    ).one()

    assert receipt.warehouse_id == warehouse.id
    assert receipt.location == "factory"
    assert tx.warehouse_id == warehouse.id
    assert material.factory_qty == Decimal("40")
    assert material.stock_qty == Decimal("0")

    detail = warehouse_purchase_view.serialize_purchase(db_session, purchase, detail=True)
    assert detail["receipts"][0]["warehouseId"] == warehouse.id
    assert detail["receipts"][0]["warehouseName"] == "指定工厂仓"


def test_consumable_receipt_rejects_goods_only_warehouse(db_session):
    warehouse = warehouse_service.create_warehouse(
        db_session,
        code=f"B2C-{uuid4().hex[:8].upper()}",
        name="仅正品B2C仓",
        warehouse_type="b2c",
        purpose="goods",
        is_sellable=True,
    )
    material = Consumable(
        code=f"HC-{uuid4().hex[:8].upper()}",
        name="测试耗材",
        unit="个",
        stock_qty=Decimal("0"),
        factory_qty=Decimal("0"),
        transit_qty=Decimal("0"),
        purchased_qty=Decimal("0"),
        used_qty=Decimal("0"),
        min_stock_qty=Decimal("0"),
        status="active",
        raw={},
    )
    db_session.add(material)
    db_session.flush()
    purchase = purchase_svc.create_purchase(
        db_session,
        request_key=str(uuid4()),
        supplier_name="测试供应商",
        ordered_on=date(2026, 9, 8),
        items=[{"consumable_id": material.id, "quantity": Decimal("10"), "unit_cost": Decimal("1")}],
        actor="pytest",
    )
    line_id = purchase_svc.serialize_purchase(db_session, purchase, detail=True)["items"][0]["id"]

    try:
        warehouse_receipt_service.receive_consumable_purchase(
            db_session,
            purchase.id,
            request_key=str(uuid4()),
            received_on=date(2026, 9, 9),
            warehouse_id=warehouse.id,
            items=[{"item_id": line_id, "quantity": Decimal("1")}],
            actor="pytest",
        )
    except ValueError as exc:
        assert "不允许存放耗材" in str(exc)
    else:
        raise AssertionError("goods-only warehouse must reject consumable receipts")
