"""利润中心单测（规格 9）：成本优先级 / 毛利诚实语义 / 多来源校验。"""
from decimal import Decimal

import pytest

from app.services.profit import effective_cost, gross_profit, upsert_cost
from app.models.profit import CostSnapshot


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
