"""利润中心单测（规格 9）：成本优先级 / 毛利诚实语义 / 多来源校验。"""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.models.catalog import ProductSku
from app.models.profit import CostSnapshot
from app.models.sales import SalesOrder, SalesOrderItem
from app.services.profit import compute, effective_cost, gross_profit, list_costs, upsert_cost


def _snap(**kw):
    return CostSnapshot(id=1, sku_id=1, period_year=2026, period_month=8, **kw)


def test_effective_cost_priority_actual_first():
    s = _snap(actual_cost=Decimal("10.00"), purch_order_cost=Decimal("9.00"),
              default_cost=Decimal("8.00"), estimated_cost=Decimal("7.50"))
    assert effective_cost(s) == (Decimal("10.00"), "actual_cost")


def test_effective_cost_fallback_to_estimated():
    s = _snap(actual_cost=None, purch_order_cost=None, default_cost=None,
              estimated_cost=Decimal("7.50"))
    assert effective_cost(s) == (Decimal("7.50"), "estimated_cost")


def test_effective_cost_missing():
    s = _snap()
    assert effective_cost(s) == (None, None)


def test_gross_profit():
    assert gross_profit(Decimal("100"), Decimal("70")) == Decimal("30.00")


def test_gross_profit_none_when_cost_missing():
    # 规格：成本缺失不显示假装精确的利润
    assert gross_profit(Decimal("100"), None) is None
    assert gross_profit(None, Decimal("70")) is None
    assert gross_profit(None, None) is None


def test_gross_profit_rounds_to_cents():
    assert gross_profit(Decimal("100.005"), Decimal("30")) == Decimal("70.01")


def test_upsert_rejects_multiple_sources():
    with pytest.raises(ValueError):
        upsert_cost(None, sku_id=1, period_year=2026, period_month=8,
                    actual_cost="10", default_cost="8")


def test_upsert_rejects_bad_period():
    with pytest.raises(ValueError):
        upsert_cost(None, sku_id=1, period_year=2026, period_month=13,
                    estimated_cost="8")


def test_upsert_keeps_multiple_sources_and_uses_priority(db_session):
    sku = ProductSku(jackyun_sku_id="test-profit-multi", sku_code="P-MULTI", sku_name="多来源")
    db_session.add(sku)
    db_session.commit()

    upsert_cost(db_session, sku_id=sku.id, period_year=2098, period_month=8,
                default_cost="8.00", source="default", actor="pytest-admin")
    upsert_cost(db_session, sku_id=sku.id, period_year=2098, period_month=8,
                actual_cost="10.00", source="actual", actor="pytest-admin")

    rows = list_costs(db_session, period_year=2098, period_month=8)
    row = next(item for item in rows if item["skuId"] == sku.id)
    assert row["values"]["default"] == "8.0000"
    assert row["values"]["actual"] == "10.0000"
    assert row["effectiveCost"] == "10.0000"
    assert row["effectiveSource"] == "实际采购结算成本"


def test_compute_filters_period_and_multiplies_unit_cost_by_quantity(db_session):
    sku = ProductSku(
        jackyun_sku_id="test-profit-calc", sku_code="P-CALC", sku_name="计算",
        default_cost=Decimal("5.00"),
    )
    db_session.add(sku)
    db_session.flush()
    august = SalesOrder(
        order_no="test-profit-2098-08", order_status="paid",
        ordered_at=datetime(2098, 8, 15, tzinfo=timezone.utc),
    )
    september = SalesOrder(
        order_no="test-profit-2098-09", order_status="paid",
        ordered_at=datetime(2098, 9, 1, tzinfo=timezone.utc),
    )
    db_session.add_all([august, september])
    db_session.flush()
    db_session.add_all([
        SalesOrderItem(order_id=august.id, sku_id=sku.id, quantity=Decimal("3"),
                       amount=Decimal("100"), discount_amount=Decimal("10")),
        SalesOrderItem(order_id=september.id, sku_id=sku.id, quantity=Decimal("99"),
                       amount=Decimal("999"), discount_amount=Decimal("0")),
    ])
    db_session.commit()

    result = compute(db_session, 2098, 8)
    assert result["netSales"] == "90.00"
    assert result["goodsCost"] == "15.00"
    assert result["grossProfit"] == "75.00"
    assert result["costMissing"] is False


def test_compute_uses_asia_shanghai_month_boundary(db_session):
    """2098-08-31 16:30 UTC 已是上海 9 月 1 日 00:30，必须归入 9 月。"""
    sku = ProductSku(
        jackyun_sku_id="test-profit-tz", sku_code="P-TZ", sku_name="时区边界",
        default_cost=Decimal("10.00"),
    )
    db_session.add(sku)
    db_session.flush()
    order = SalesOrder(
        order_no="test-profit-tz-boundary", order_status="paid",
        ordered_at=datetime(2098, 8, 31, 16, 30, tzinfo=timezone.utc),
    )
    db_session.add(order)
    db_session.flush()
    db_session.add(SalesOrderItem(
        order_id=order.id, sku_id=sku.id, quantity=Decimal("2"),
        amount=Decimal("50"), discount_amount=Decimal("0"),
    ))
    db_session.commit()

    august = compute(db_session, 2098, 8)
    september = compute(db_session, 2098, 9)
    assert august["netSales"] is None
    assert september["netSales"] == "50.00"
    assert september["goodsCost"] == "20.00"
    assert september["grossProfit"] == "30.00"


def test_compute_with_unmapped_item_does_not_publish_partial_cost(db_session):
    order = SalesOrder(
        order_no="test-profit-unmapped-2098", pay_status="paid",
        ordered_at=datetime(2098, 7, 10, tzinfo=timezone.utc),
    )
    db_session.add(order)
    db_session.flush()
    item = SalesOrderItem(
        order_id=order.id, sku_id=None, quantity=Decimal("1"),
        amount=Decimal("20"), discount_amount=Decimal("0"),
    )
    db_session.add(item)
    db_session.commit()

    result = compute(db_session, 2098, 7)
    assert result["netSales"] == "20.00"
    assert result["goodsCost"] is None
    assert result["grossProfit"] is None
    assert result["unmappedItems"] == [item.id]
    assert result["costMissing"] is True
