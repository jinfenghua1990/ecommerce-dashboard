from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.models.alibaba1688_import import Alibaba1688FileImport, Alibaba1688Order
from app.models.purchase import ExternalPurchaseOrder, JackyunPurchaseOrder, JackyunPurchaseOrderLink
from app.models.tax import TaxInvoice, TaxInvoiceImport, TaxInvoiceLink
from app.services import invoice_reconciliation
from app.services import procurement_chain_service as chain
from app.services import procurement_workbench_service as workbench
from app.services import tax_invoice_service as tax_service


def _order(db_session, no: str, amount: str = "100"):
    batch = Alibaba1688FileImport(original_name=f"{no}.xlsx", stored_path=f"/tmp/{no}.xlsx", sha256=f"sha-{no}", lifecycle="active")
    db_session.add(batch); db_session.flush()
    source = Alibaba1688Order(external_order_id=no, seller_company_name="最终预检供应商", actual_payment=Decimal(amount), order_time=datetime(2026, 8, 1, tzinfo=timezone.utc), import_id=batch.id)
    external = ExternalPurchaseOrder(external_order_id=no, supplier_name="最终预检供应商", paid_amount=Decimal(amount), purchase_status="confirmed", ordered_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    db_session.add_all([source, external]); db_session.flush()
    return source, external


def test_void_invoice_with_explicit_ref_never_links(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(tax_service.settings, "DATA_DIR", str(tmp_path))
    source, _ = _order(db_session, "FINAL-VOID-REF-001")
    csv = ("发票号码,开票日期,销售方名称,价税合计,发票状态,进销项,关联订单号\n" f"VOID-001,2026-08-05,最终预检供应商,100,作废,进项,{source.external_order_id}\n").encode()
    batch, _ = tax_service.import_export(db_session, content=csv, original_name="void.csv", actor="pytest", auto_confirm=True)
    invoice = db_session.query(TaxInvoice).filter_by(invoice_number="VOID-001").one()
    assert batch.matched_row_count == 0
    assert invoice.status == "void" and invoice.match_status == "unmatched"
    assert db_session.query(TaxInvoiceLink).filter_by(invoice_id=invoice.id).count() == 0


def test_invalid_invoice_not_counted_or_verified(db_session):
    source, _ = _order(db_session, "FINAL-VOID-CHAIN-001")
    invoice = TaxInvoice(invoice_key="final|void-chain", invoice_number="VOID-CHAIN-001", direction="input", status="void", issue_date=datetime(2026, 8, 5, tzinfo=timezone.utc), seller_name="最终预检供应商", total_amount=Decimal("100"), match_status="matched", verified=True, verified_month="2026-08")
    db_session.add(invoice); db_session.flush()
    link = TaxInvoiceLink(invoice_id=invoice.id, target_type="alibaba1688_order", target_id=source.id, allocated_amount=Decimal("100"), match_method="auto", confidence=Decimal("1"), confirmed=True)
    db_session.add(link); db_session.commit()
    detail = workbench.workbench(db_session, source.id)
    assert detail["detail"]["invoice"] == []
    assert detail["order"]["invoiceStatus"] == "pending"
    assert detail["order"]["invoicedAmount"] == 0.0
    link.confirmed = False; db_session.commit()
    chain.auto_confirm_pending_links(db_session, actor="pytest"); db_session.refresh(link)
    assert link.confirmed is False and link.match_method == "invalid_invoice"
    with pytest.raises(ValueError, match="不能标记认证"):
        chain.ProcurementChainMatcher(db_session).set_invoice_verified(invoice.id, True, "2026-08")


def test_draft_invoice_link_stays_inactive_until_confirm(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(tax_service.settings, "DATA_DIR", str(tmp_path))
    _, external = _order(db_session, "FINAL-DRAFT-001")
    csv = ("发票号码,开票日期,销售方名称,价税合计,发票状态,进销项,关联订单号\n" f"DRAFT-001,2026-08-05,最终预检供应商,100,正常,进项,{external.external_order_id}\n").encode()
    batch, _ = tax_service.import_export(db_session, content=csv, original_name="draft.csv", actor="pytest", auto_confirm=False)
    invoice = db_session.query(TaxInvoice).filter_by(invoice_number="DRAFT-001").one()
    link = db_session.query(TaxInvoiceLink).filter_by(invoice_id=invoice.id).one()
    assert batch.lifecycle == "draft" and batch.matched_row_count == 0
    assert link.confirmed is False and invoice.match_status == "unmatched"
    detail = workbench.workbench(db_session, -external.id)
    assert detail["order"]["invoiceStatus"] == "pending"
    chain.auto_confirm_pending_links(db_session, actor="pytest"); db_session.refresh(link)
    assert link.confirmed is False
    tax_service.confirm_import(db_session, batch.id, "pytest")
    chain.auto_confirm_pending_links(db_session, actor="pytest"); db_session.refresh(link)
    assert link.confirmed is True


def test_invoice_reconciliation_ignores_draft_tax_batch(db_session):
    _, external = _order(db_session, "FINAL-RECON-DRAFT-001")
    batch = TaxInvoiceImport(original_name="recon-draft.xlsx", stored_path="/tmp/recon-draft.xlsx", sha256="final-recon-draft", lifecycle="draft")
    db_session.add(batch); db_session.flush()
    invoice = TaxInvoice(invoice_key="final|recon-draft", invoice_number="RECON-DRAFT-001", direction="input", status="issued", issue_date=datetime(2026, 8, 5, tzinfo=timezone.utc), seller_name=external.supplier_name, total_amount=Decimal("100"), source_import_id=batch.id, source_row_index=1)
    db_session.add(invoice); db_session.commit()
    result = invoice_reconciliation.reconcile(db_session, supplier=external.supplier_name)
    assert "RECON-DRAFT-001" not in str(result)
    batch.lifecycle = "active"; db_session.commit()
    result = invoice_reconciliation.reconcile(db_session, supplier=external.supplier_name)
    assert "RECON-DRAFT-001" in str(result)



def test_unknown_status_invoice_never_enters_procurement(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(tax_service.settings, "DATA_DIR", str(tmp_path))
    source, _ = _order(db_session, "FINAL-UNKNOWN-001")
    csv = ("发票号码,开票日期,销售方名称,价税合计,发票状态,进销项,关联订单号\n"
           f"UNKNOWN-001,2026-08-05,最终预检供应商,100,,进项,{source.external_order_id}\n").encode()
    tax_service.import_export(db_session, content=csv, original_name="unknown.csv", actor="pytest", auto_confirm=True)
    invoice = db_session.query(TaxInvoice).filter_by(invoice_number="UNKNOWN-001").one()
    assert invoice.status == "unknown"
    assert invoice.match_status != "matched"
    assert db_session.query(TaxInvoiceLink).filter_by(invoice_id=invoice.id).count() == 0
    with pytest.raises(ValueError):
        chain.ProcurementChainMatcher(db_session).set_invoice_verified(invoice.id, True, "2026-08")


def test_jky_purchase_ref_resolves_to_source_purchase_order(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(tax_service.settings, "DATA_DIR", str(tmp_path))
    _, external = _order(db_session, "FINAL-JKY-SOURCE-001")
    jpo = JackyunPurchaseOrder(jackyun_purch_id="jky-final-001", purch_no="JH-FINAL-001",
                               supplier_name="最终预检供应商", amount=Decimal("100"), status="有效")
    db_session.add(jpo)
    db_session.flush()
    db_session.add(JackyunPurchaseOrderLink(po_id=external.id, jackyun_po_id=jpo.id))
    db_session.commit()
    csv = ("发票号码,开票日期,销售方名称,价税合计,发票状态,进销项,关联订单号\n"
           "JKY-REF-001,2026-08-05,最终预检供应商,100,正常,进项,JH-FINAL-001\n").encode()
    tax_service.import_export(db_session, content=csv, original_name="jky-ref.csv", actor="pytest", auto_confirm=True)
    invoice = db_session.query(TaxInvoice).filter_by(invoice_number="JKY-REF-001").one()
    links = db_session.query(TaxInvoiceLink).filter_by(invoice_id=invoice.id).all()
    assert len(links) == 1
    assert links[0].target_type == "external_purchase_order"
    assert links[0].target_id == external.id


def test_legacy_jky_invoice_link_migrates_to_source_purchase_order(db_session):
    _, external = _order(db_session, "FINAL-JKY-LEGACY-001")
    jpo = JackyunPurchaseOrder(jackyun_purch_id="jky-legacy-001", purch_no="JH-LEGACY-001",
                               supplier_name="最终预检供应商", amount=Decimal("100"), status="有效")
    db_session.add(jpo)
    db_session.flush()
    db_session.add(JackyunPurchaseOrderLink(po_id=external.id, jackyun_po_id=jpo.id))
    db_session.flush()
    invoice = TaxInvoice(invoice_key="final|jky-legacy", invoice_number="JKY-LEGACY-INV", direction="input",
                         status="issued", total_amount=Decimal("100"), match_status="matched")
    db_session.add(invoice)
    db_session.flush()
    link = TaxInvoiceLink(invoice_id=invoice.id, target_type="jackyun_purchase_order", target_id=jpo.id,
                          allocated_amount=Decimal("100"), match_method="source_ref",
                          confidence=Decimal("1"), confirmed=True)
    db_session.add(link)
    db_session.commit()
    chain.auto_confirm_pending_links(db_session, actor="pytest")
    db_session.refresh(link)
    assert link.target_type == "external_purchase_order"
    assert link.target_id == external.id
    assert link.confirmed is True



def test_unknown_direction_invoice_stays_review_only(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(tax_service.settings, "DATA_DIR", str(tmp_path))
    source, _ = _order(db_session, "FINAL-DIRECTION-UNKNOWN-001")
    csv = (
        "发票号码,开票日期,销售方名称,价税合计,发票状态,关联订单号\n"
        f"DIR-UNKNOWN-001,2026-08-05,最终预检供应商,100,正常,{source.external_order_id}\n"
    ).encode()
    tax_service.import_export(
        db_session, content=csv, original_name="direction-unknown.csv", actor="pytest", auto_confirm=True
    )
    invoice = db_session.query(TaxInvoice).filter_by(invoice_number="DIR-UNKNOWN-001").one()
    assert invoice.direction == "unknown"
    assert invoice.match_status == "needs_review"
    assert db_session.query(TaxInvoiceLink).filter_by(invoice_id=invoice.id).count() == 0
