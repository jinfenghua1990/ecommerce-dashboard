from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.models.tax import TaxInvoice, TaxInvoiceImport, TaxInvoiceImportRecord
from app.services.tax_finance_summary_service import build_finance_summary, to_finance_csv


def _batch(db_session) -> TaxInvoiceImport:
    batch = TaxInvoiceImport(
        original_name=f"finance-summary-{uuid4().hex}.xlsx",
        stored_path=f"/tmp/{uuid4().hex}.xlsx",
        sha256=uuid4().hex + uuid4().hex,
        size=100,
        source_system="tax_export",
        period_year=2026,
        period_month=8,
        status="parsed",
        lifecycle="active",
        row_count=10,
        recognized_row_count=10,
    )
    db_session.add(batch)
    db_session.flush()
    return batch


def _invoice_detail(
    db_session,
    batch: TaxInvoiceImport,
    *,
    row_index: int,
    goods_name: str,
    category: str,
    tax_rate: str,
    quantity: str,
    unit: str,
    amount: str,
    tax: str,
) -> None:
    total = Decimal(amount) + Decimal(tax)
    invoice = TaxInvoice(
        invoice_key=f"pytest-finance|{uuid4().hex}",
        invoice_number=f"FS-{uuid4().hex[:12]}",
        direction="output",
        status="issued",
        issue_date=datetime(2026, 8, 15, tzinfo=timezone.utc),
        amount_excl_tax=Decimal(amount),
        tax_amount=Decimal(tax),
        total_amount=total,
        source_system="tax_export",
        source_import_id=batch.id,
        source_row_index=row_index,
        raw={},
    )
    db_session.add(invoice)
    db_session.flush()
    db_session.add(TaxInvoiceImportRecord(
        import_id=batch.id,
        row_index=row_index,
        invoice_id=invoice.id,
        recognition_status="recognized",
        row_status="active",
        payload={
            "税收分类名称": category,
            "货物或应税劳务、服务名称": goods_name,
            "单位": unit,
            "数量": quantity,
            "不含税单价": str(Decimal(amount) / Decimal(quantity)),
            "不含税金额": amount,
            "税率": tax_rate,
            "税额": tax,
            "价税合计": str(total),
        },
    ))
    db_session.flush()


def test_finance_summary_groups_detail_into_broad_category_and_keeps_tax_rates_separate(db_session):
    batch = _batch(db_session)
    _invoice_detail(
        db_session, batch, row_index=1, goods_name="气泡饮料A", category="软饮料",
        tax_rate="13%", quantity="10", unit="箱", amount="1000", tax="130",
    )
    _invoice_detail(
        db_session, batch, row_index=2, goods_name="茶饮料B", category="软饮料",
        tax_rate="13%", quantity="20", unit="箱", amount="2000", tax="260",
    )
    _invoice_detail(
        db_session, batch, row_index=3, goods_name="其他税率饮料", category="软饮料",
        tax_rate="9%", quantity="5", unit="箱", amount="500", tax="45",
    )

    report = build_finance_summary(db_session, 2026, 8)
    assert report["readyForFinanceDelivery"] is True
    assert report["summary"]["detailCount"] == 3
    assert report["summary"]["categoryRowCount"] == 2

    rows = {(row["accountingCategory"], row["taxRate"]): row for row in report["categories"]}
    row_13 = rows[("软饮料", "13%")]
    assert row_13["quantity"] == "30"
    assert row_13["unit"] == "箱"
    assert row_13["amountExclTax"] == "3000.00"
    assert row_13["taxAmount"] == "390.00"
    assert row_13["totalAmount"] == "3390.00"
    assert row_13["detailCount"] == 2

    row_9 = rows[("软饮料", "9%")]
    assert row_9["quantity"] == "5"
    assert row_9["amountExclTax"] == "500.00"
    assert row_9["taxAmount"] == "45.00"

    csv_text = to_finance_csv(report).decode("utf-8-sig")
    assert "软饮料" in csv_text
    assert "气泡饮料A" not in csv_text
    assert "茶饮料B" not in csv_text
    assert {item["goodsName"] for item in report["details"]} >= {"气泡饮料A", "茶饮料B"}
    assert all(item["deletable"] is False for item in report["details"])


def test_official_invoice_star_prefix_can_supply_broad_category(db_session):
    batch = _batch(db_session)
    _invoice_detail(
        db_session,
        batch,
        row_index=1,
        goods_name="*软饮料*咖啡饮料",
        category="",
        tax_rate="13%",
        quantity="12",
        unit="箱",
        amount="1200",
        tax="156",
    )

    report = build_finance_summary(db_session, 2026, 8)
    assert report["readyForFinanceDelivery"] is True
    assert report["categories"][0]["accountingCategory"] == "软饮料"
    assert report["details"][0]["goodsName"] == "*软饮料*咖啡饮料"


def test_finance_summary_never_forces_mixed_units_into_one_quantity(db_session):
    batch = _batch(db_session)
    _invoice_detail(
        db_session, batch, row_index=1, goods_name="饮料箱装", category="软饮料",
        tax_rate="13%", quantity="10", unit="箱", amount="1000", tax="130",
    )
    _invoice_detail(
        db_session, batch, row_index=2, goods_name="饮料瓶装", category="软饮料",
        tax_rate="13%", quantity="100", unit="瓶", amount="1000", tax="130",
    )

    report = build_finance_summary(db_session, 2026, 8)
    row = report["categories"][0]
    assert row["accountingCategory"] == "软饮料"
    assert row["unit"] == "多单位"
    assert row["quantity"] is None
    assert row["quantityComplete"] is False


def test_missing_accounting_category_blocks_finance_delivery(db_session):
    batch = _batch(db_session)
    _invoice_detail(
        db_session, batch, row_index=1, goods_name="未分类商品", category="",
        tax_rate="13%", quantity="10", unit="箱", amount="1000", tax="130",
    )

    report = build_finance_summary(db_session, 2026, 8)
    assert report["readyForFinanceDelivery"] is False
    assert report["summary"]["blockerCount"] == 1
    assert "禁止自动猜分类" in report["blockers"][0]["reasons"][0]


def test_tax_original_detail_delete_endpoint_is_blocked(client):
    response = client.delete("/api/v1/tax-invoices/imports/1/records/1")
    assert response.status_code == 409
    assert "税务原始明细不可删除" in response.json()["detail"]
