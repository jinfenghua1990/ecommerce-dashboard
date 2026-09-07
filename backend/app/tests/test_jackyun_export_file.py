"""吉客云客户端官方导出文件解析测试，不使用真实业务数据。"""
import io
from pathlib import Path

import pytest

from app.adapters.jackyun_export_file import parse_jackyun_export
from app.config import settings
from app.models.jackyun_import import JackyunFileImport, JackyunFileImportRecord
from app.models.sales import SalesOrder
from app.services import jackyun_file_import_service as service


def _xlsx(rows: list[list[object]]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def test_parse_xlsx_finds_header_after_export_title():
    content = _xlsx([
        ["吉客云库存明细表"],
        ["导出时间", "2026-09-02"],
        ["货品编号", "货品名称", "可用库存", "仓库名称"],
        ["SKU-001", "测试商品", 12, "主仓"],
    ])
    parsed = parse_jackyun_export(content, "库存明细.xlsx", max_rows=100)
    assert parsed.report_type == "inventory"
    assert parsed.headers == ["货品编号", "货品名称", "可用库存", "仓库名称"]
    assert parsed.rows == [{"货品编号": "SKU-001", "货品名称": "测试商品", "可用库存": "12", "仓库名称": "主仓"}]


def test_parse_csv_classifies_sales_and_keeps_original_headers():
    content = "销售订单明细\n订单编号,订单状态,店铺名称,下单时间\nSO-001,已付款,测试店,2026-09-02\n".encode("utf-8")
    parsed = parse_jackyun_export(content, "销售订单.csv", max_rows=100)
    assert parsed.report_type == "sales"
    assert parsed.rows[0]["订单编号"] == "SO-001"


def test_parser_rejects_unsupported_extension():
    with pytest.raises(ValueError, match="XLSX 或 CSV"):
        parse_jackyun_export(b"not a pdf", "导出.pdf", max_rows=100)


def test_file_import_archives_and_deduplicates(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    content = _xlsx([
        ["订单编号", "订单状态", "店铺名称", "下单时间"],
        ["SO-ARCHIVE-001", "已付款", "测试店", "2026-09-02"],
    ])
    row, duplicate = service.import_export(
        db_session, content=content, original_name="销售订单.xlsx", actor="pytest-admin"
    )
    assert duplicate is False
    assert row.report_type == "sales"
    assert row.row_count == 1
    assert Path(row.stored_path).is_file()
    assert db_session.query(JackyunFileImportRecord).filter_by(import_id=row.id).count() == 1
    # 首次本地文件只做真实表头/原始行入 staging；未经样本映射不得伪造业务订单。
    assert db_session.query(SalesOrder).filter_by(order_no="SO-ARCHIVE-001").count() == 0

    same, duplicate = service.import_export(
        db_session, content=content, original_name="重命名销售订单.xlsx", actor="pytest-admin"
    )
    assert duplicate is True
    assert same.id == row.id


def test_upload_endpoint_returns_parsed_metadata(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    content = _xlsx([
        ["货品编号", "货品名称", "库存数量"],
        ["SKU-UPLOAD-001", "上传测试商品", 8],
    ])
    response = client.post(
        "/api/v1/jackyun-files/imports",
        files={"file": ("库存.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["duplicate"] is False
    assert body["import"]["reportType"] == "inventory"
    assert body["import"]["stagedRowCount"] == 1


def test_upload_endpoint_enforces_its_own_smaller_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_JACKYUN_IMPORT_BYTES", 3)
    response = client.post(
        "/api/v1/jackyun-files/imports",
        files={"file": ("too-large.csv", b"1234", "text/csv")},
    )
    assert response.status_code == 413
