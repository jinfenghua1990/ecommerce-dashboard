from datetime import datetime, timezone
from uuid import uuid4

from app.models.purchase import ExternalPurchaseOrder
from app.services.production_purchase_view import list_production_purchase_rows


def _po(db_session, *, suffix: str, kind: str, status: str) -> ExternalPurchaseOrder:
    row = ExternalPurchaseOrder(
        external_order_id=f"PYTEST-PROD-{suffix}-{uuid4().hex[:8]}",
        platform="1688",
        supplier_name=f"测试工厂-{suffix}",
        title=f"测试正品采购-{suffix}",
        ordered_at=datetime.now(timezone.utc),
        order_kind_override=kind,
        purchase_status=status,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def test_production_purchase_view_archives_existing_goods_and_excludes_consumables(db_session):
    producing = _po(db_session, suffix="PRODUCING", kind="goods", status="producing")
    shipped = _po(db_session, suffix="SHIPPED", kind="goods", status="shipped")
    consumable = _po(db_session, suffix="HC", kind="consumable", status="producing")

    result = list_production_purchase_rows(db_session, group="all", limit=100)
    by_no = {row["orderNo"]: row for row in result["rows"]}

    assert producing.external_order_id in by_no
    assert shipped.external_order_id in by_no
    assert consumable.external_order_id not in by_no
    assert by_no[producing.external_order_id]["stage"] == "producing"
    assert by_no[producing.external_order_id]["archiveGroup"] == "production"
    assert by_no[shipped.external_order_id]["stage"] == "shipped"
    assert by_no[shipped.external_order_id]["archiveGroup"] == "transit"

    transit = list_production_purchase_rows(db_session, group="transit", limit=100)
    transit_nos = {row["orderNo"] for row in transit["rows"]}
    assert shipped.external_order_id in transit_nos
    assert producing.external_order_id not in transit_nos
    assert consumable.external_order_id not in transit_nos


def test_production_purchase_view_uses_inbound_fact_for_legacy_status(db_session):
    row = _po(db_session, suffix="WAIT", kind="goods", status="confirmed")
    result = list_production_purchase_rows(db_session, group="production", q=row.external_order_id, limit=10)
    assert len(result["rows"]) == 1
    assert result["rows"][0]["stageLabel"] == "待生产"
