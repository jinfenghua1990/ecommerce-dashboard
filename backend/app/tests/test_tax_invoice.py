from app.adapters.tax_invoice_file import parse_tax_invoice_export
from app.services import tax_invoice_service as service
from uuid import uuid4


CSV = """税务系统发票清单
发票号码,发票代码,开票日期,销售方名称,销售方识别号,购买方名称,购买方识别号,金额,税额,价税合计,发票状态,进销项,关联订单号
INV-001, CODE-1,2026-09-01,供应商甲,91330000000000001A,本公司,91330000000000002B,100,13,113,正常,进项,PO-001
INV-002,CODE-2,2026-09-02,本公司,91330000000000002B,客户乙,91330000000000003C,200,26,226,正常,销项,
"""


def test_tax_export_auto_detects_fields_and_direction():
    parsed = parse_tax_invoice_export(CSV.encode(), "tax-list.csv", max_rows=100)

    assert parsed.sheet_name == "CSV"
    assert parsed.mapping["invoice_number"] == "发票号码"
    assert parsed.mapping["total_amount"] == "价税合计"
    assert parsed.direction_hint == "unknown"
    assert len(parsed.rows) == 2


def test_tax_import_normalizes_rows_is_idempotent_and_links_explicit_order(
    db_session, monkeypatch, tmp_path
):
    from app.models.purchase import ExternalPurchaseOrder
    from app.models.tax import TaxInvoice, TaxInvoiceImport, TaxInvoiceImportRecord, TaxInvoiceLink

    monkeypatch.setattr(service.settings, "DATA_DIR", str(tmp_path))
    token = uuid4().hex[:10]
    order_no = f"PO-TEST-{token}"
    invoice_1 = f"INV-TEST-A-{token}"
    invoice_2 = f"INV-TEST-B-{token}"
    content = CSV.replace("PO-001", order_no).replace("INV-001", invoice_1).replace("INV-002", invoice_2).encode()
    baseline_invoices = db_session.query(TaxInvoice).count()
    po = ExternalPurchaseOrder(external_order_id=order_no, paid_amount="113")
    db_session.add(po)
    db_session.commit()

    batch, duplicate = service.import_export(
        db_session,
        content=content,
        original_name="tax-list.csv",
        actor="pytest",
        period_year=2026,
        period_month=9,
    )
    same_batch, same_duplicate = service.import_export(
        db_session,
        content=content,
        original_name="tax-list.csv",
        actor="pytest",
    )

    assert duplicate is False
    assert same_duplicate is True
    assert same_batch.id == batch.id
    assert batch.row_count == 2
    assert batch.recognized_row_count == 2
    assert batch.needs_review_count == 0
    assert db_session.query(TaxInvoice).count() == baseline_invoices + 2
    assert db_session.query(TaxInvoiceImport).filter_by(id=batch.id).one().status == "parsed"
    assert db_session.query(TaxInvoiceImportRecord).filter_by(import_id=batch.id).count() == 2
    invoice = db_session.query(TaxInvoice).filter_by(invoice_number=invoice_1).one()
    assert invoice.direction == "input"
    assert invoice.status == "issued"
    assert str(invoice.total_amount) == "113.0000"
    assert invoice.match_status == "matched"
    assert db_session.query(TaxInvoiceLink).filter_by(invoice_id=invoice.id).count() == 1

    db_session.query(TaxInvoiceLink).filter_by(invoice_id=invoice.id).delete()
    db_session.query(TaxInvoiceImportRecord).filter_by(import_id=batch.id).delete()
    db_session.query(TaxInvoice).filter(TaxInvoice.id.in_((
        row.id for row in db_session.query(TaxInvoice).filter_by(source_import_id=batch.id).all()
    ))).delete(synchronize_session=False)
    db_session.delete(batch)
    db_session.delete(po)
    db_session.commit()


def test_tax_import_keeps_unrecognized_row_for_review(db_session, monkeypatch, tmp_path):
    from app.models.tax import TaxInvoice, TaxInvoiceImportRecord

    monkeypatch.setattr(service.settings, "DATA_DIR", str(tmp_path))
    content = "发票号码,开票日期,价税合计\nINV-003,,\n".encode()
    batch, _ = service.import_export(
        db_session, content=content, original_name="needs-review.csv", actor="pytest"
    )

    assert batch.status == "needs_review"
    assert batch.recognized_row_count == 0
    assert batch.needs_review_count == 1
    row = db_session.query(TaxInvoiceImportRecord).filter_by(import_id=batch.id).one()
    assert row.recognition_status == "needs_review"
    assert row.invoice_id is None
    assert db_session.query(TaxInvoice).filter_by(invoice_number="INV-003").count() == 0

    db_session.query(TaxInvoiceImportRecord).filter_by(import_id=batch.id).delete()
    db_session.delete(batch)
    db_session.commit()
