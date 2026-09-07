from decimal import Decimal
from uuid import uuid4

from app.models.catalog import ProductSku
from app.models.consumable import Consumable, ConsumableSkuMapping
from app.services.production_service import (
    cancel_production_order,
    create_production_order,
    production_order_detail,
    recalculate_production_materials,
)


def _sku(db_session, suffix: str) -> ProductSku:
    row = ProductSku(
        jackyun_sku_id=f"pytest-jky-{suffix}-{uuid4().hex}",
        sku_code=f"PYTEST-SKU-{suffix}-{uuid4().hex[:8]}",
        sku_name=f"测试生产 SKU {suffix}",
        unit="盒",
        status="active",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _consumable(db_session, suffix: str, stock: str) -> Consumable:
    row = Consumable(
        code=f"PYTEST-HC-{suffix}-{uuid4().hex[:8]}",
        name=f"测试耗材 {suffix}",
        consumable_type="box",
        unit="个",
        stock_qty=Decimal(stock),
        factory_qty=Decimal("0"),
        transit_qty=Decimal("0"),
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_production_material_reservation_does_not_double_allocate(db_session):
    sku = _sku(db_session, "A")
    material = _consumable(db_session, "A", "100")
    db_session.add(
        ConsumableSkuMapping(
            consumable_id=material.id,
            sku_id=sku.id,
            usage_per_unit=Decimal("2"),
            note="pytest",
        )
    )
    db_session.flush()

    first = create_production_order(
        db_session,
        factory_name="测试工厂一",
        items=[{"sku_id": sku.id, "quantity": Decimal("40")}],
        actor="pytest",
    )
    first_detail = production_order_detail(db_session, first)
    assert first_detail["materials"][0]["requiredQty"] == "80.0000"
    assert first_detail["materials"][0]["reservedQty"] == "80.0000"
    assert first_detail["materials"][0]["shortageQty"] == "0.0000"

    # 第二张单需要 40 个耗材，但第一张已经预占 80；自有仓 100，所以只能再预占 20。
    second = create_production_order(
        db_session,
        factory_name="测试工厂二",
        items=[{"sku_id": sku.id, "quantity": Decimal("20")}],
        actor="pytest",
    )
    second_detail = production_order_detail(db_session, second)
    assert second_detail["materials"][0]["requiredQty"] == "40.0000"
    assert second_detail["materials"][0]["reservedQty"] == "20.0000"
    assert second_detail["materials"][0]["shortageQty"] == "20.0000"
    assert second_detail["materials"][0]["state"] == "shortage"

    # “预占”不能直接扣实际自有仓库存。
    db_session.refresh(material)
    assert material.stock_qty == Decimal("100")

    # 取消第一张未发料生产单后，第二张重算应拿到完整 40 的预占。
    cancel_production_order(db_session, first.id)
    second = recalculate_production_materials(db_session, second.id)
    second_detail = production_order_detail(db_session, second)
    assert second_detail["materials"][0]["reservedQty"] == "40.0000"
    assert second_detail["materials"][0]["shortageQty"] == "0.0000"
    assert second_detail["materials"][0]["state"] == "reserved"

    db_session.refresh(material)
    assert material.stock_qty == Decimal("100")
