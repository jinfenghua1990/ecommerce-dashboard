from datetime import date
from decimal import Decimal

from app.services.reconciliation import (
    build_fingerprint,
    match_platform,
    score_match,
)

RULES = [
    ("江苏银行-平台交易资金专户（抖音）", "contains", "抖音"),
    ("上海寻梦信息技术有限公司", "contains", "拼多多"),
    ("某公司", "equals", "测试平台"),
]


def test_platform_contains_match():
    assert match_platform("江苏银行-平台交易资金专户（抖音）0123", RULES) == "抖音"
    assert match_platform("上海寻梦信息技术有限公司上海分公司", RULES) == "拼多多"


def test_platform_equals_match_only_exact():
    assert match_platform("某公司", RULES) == "测试平台"
    assert match_platform("某公司子公司", RULES) is None  # equals 不应 contains 命中


def test_platform_no_match():
    assert match_platform("张三", RULES) is None


def test_fingerprint_stable_and_voucher_based():
    a = build_fingerprint("ZJRC-001", "2026-09-01", "12000.00", "V123")
    b = build_fingerprint("ZJRC-001", "2026-09-01", "12000", "V123")
    assert a == b  # 金额表示差异不影响指纹


def test_fingerprint_fallback_without_voucher():
    a = build_fingerprint("ZJRC-001", "2026-09-01", "100", "", "抖音", "货款")
    b = build_fingerprint("ZJRC-001", "2026-09-01", "100.00", "", "抖音", "货款")
    assert a == b
    c = build_fingerprint("ZJRC-001", "2026-09-01", "100", "", "抖音", "其他")
    assert a != c  # 摘要参与 fallback hash


def _score(**kw):
    base = dict(
        txn_date=date(2026, 8, 28), txn_amount="12000", counterparty_name="江苏银行-平台交易资金专户（抖音）",
        summary="7月货款", settlement_platform="抖音", settlement_store="",
        settlement_expected="12000", settlement_year=2026, settlement_month=8,
        platform_of_txn="抖音",
    )
    base.update(kw)
    return score_match(**base)


def test_score_high_all_signals():
    r = _score()
    assert r["score"] == 95
    assert r["confidence"] == "high"


def test_score_medium_amount_only():
    """只有金额一致（无规则、账期不符、户名无提示）→ 低置信，不能只靠金额。"""
    r = _score(platform_of_txn=None, txn_date=date(2026, 5, 1), counterparty_name="张三")
    assert r["score"] == 40
    assert r["confidence"] == "low"


def test_score_medium_platform_and_date():
    r = _score(txn_amount="11000")
    assert r["score"] == 55
    assert r["confidence"] == "medium"


def test_score_low_amount_close_but_nothing_else():
    r = _score(txn_amount="12060", platform_of_txn=None, txn_date=date(2026, 5, 1),
               counterparty_name="张三")
    assert r["score"] == 20
    assert r["confidence"] == "low"


def test_score_in_period_vs_near_end():
    in_period = _score()
    near_end = _score(txn_date=date(2026, 9, 3))
    assert in_period["score"] == 95
    assert near_end["score"] == 90  # 账期内+15 vs 距期末≤7天+10
