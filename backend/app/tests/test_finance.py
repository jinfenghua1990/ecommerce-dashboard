from types import SimpleNamespace

import pytest

from app.services import finance_service
from app.services.finance_service import DEFAULT_REQUIRED, evaluate_completeness


def f(category: str):
    return SimpleNamespace(category=category)


def test_incomplete_without_required_monthly_files():
    """新账期固定要求银行资料 + 系统生成的销售汇总。"""
    status, summary = evaluate_completeness([], DEFAULT_REQUIRED)
    assert status == "INCOMPLETE"
    assert summary["missing"] == {"bank": 1, "sales_summary": 1}


def test_ready_with_bank_and_sales_summary():
    status, summary = evaluate_completeness(
        [f("bank"), f("sales_summary")], DEFAULT_REQUIRED
    )
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


@pytest.mark.parametrize("year,month", [(1899, 1), (3000, 1), (2026, 0), (2026, 13)])
def test_validate_period_rejects_out_of_range_values(year, month):
    with pytest.raises(ValueError):
        finance_service.validate_period(year, month)


def test_managed_data_file_rejects_path_outside_data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    inside = data_dir / "finance" / "source.xlsx"
    inside.parent.mkdir()
    inside.write_bytes(b"inside")
    outside = tmp_path / "outside.xlsx"
    outside.write_bytes(b"outside")
    monkeypatch.setattr(finance_service.settings, "DATA_DIR", str(data_dir))

    assert finance_service.managed_data_file(inside, label="归档文件") == inside.resolve()
    with pytest.raises(RuntimeError, match="受管数据目录"):
        finance_service.managed_data_file(outside, label="归档文件")


def test_exclusive_archive_write_never_overwrites_existing_file(tmp_path):
    target = tmp_path / "original.xlsx"
    finance_service._write_new_file(target, b"first")
    with pytest.raises(FileExistsError):
        finance_service._write_new_file(target, b"second")
    assert target.read_bytes() == b"first"


def test_store_upload_enforces_maximum_size_before_database_write(monkeypatch):
    monkeypatch.setattr(finance_service.settings, "MAX_UPLOAD_BYTES", 3)
    with pytest.raises(ValueError, match="大小上限"):
        finance_service.store_upload(
            object(), company="测试公司", year=2026, month=9, category="other",
            original_name="too-large.txt", content=b"1234",
        )
