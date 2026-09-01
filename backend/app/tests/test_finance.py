from types import SimpleNamespace

import pytest

from app.services.finance_service import DEFAULT_REQUIRED, evaluate_completeness


def f(category: str):
    return SimpleNamespace(category=category)


def test_incomplete_without_bank_file():
    """规格 E2E 2：账期资料缺失 → INCOMPLETE → 不打包。"""
    status, summary = evaluate_completeness([], DEFAULT_REQUIRED)
    assert status == "INCOMPLETE"
    assert summary["missing"] == {"bank": 1}


def test_ready_with_bank_file():
    status, summary = evaluate_completeness([f("bank")], DEFAULT_REQUIRED)
    assert status == "READY"
    assert summary["missing"] == {}


def test_extra_categories_do_not_help():
    status, _ = evaluate_completeness([f("invoice"), f("other")], DEFAULT_REQUIRED)
    assert status == "INCOMPLETE"


def test_multiple_bank_files_count():
    status, summary = evaluate_completeness([f("bank"), f("bank")], {"bank": 2})
    assert status == "READY"


def test_partial_bank_shortfall():
    status, summary = evaluate_completeness([f("bank")], {"bank": 2})
    assert status == "INCOMPLETE"
    assert summary["missing"] == {"bank": 1}


def test_unknown_category_ignored():
    status, _ = evaluate_completeness([f("weird")], {"bank": 1})
    assert status == "INCOMPLETE"


@pytest.mark.parametrize("required", [{}, None])
def test_empty_required_always_ready(required):
    status, _ = evaluate_completeness([], required)
    assert status == "READY"
