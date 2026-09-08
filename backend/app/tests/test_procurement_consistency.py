from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.catalog import ProductSku
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.models.purchase import (
    ExternalPurchaseOrder,
    JackyunPurchaseOrder,
    JackyunPurchaseOrderLink,
    PurchaseAllocationItem,
)
from app.models.tax import TaxInvoice, TaxInvoiceLink
from app.services.procurement_consistency import (
    assign_shared_inbound_item,
    completion_snapshot,
    jackyun_group_summary,
    merge_orders_to_jackyun_po,
)


def _po(no: str, amount: str, platform: str = "1688") -> ExternalPurchaseOrder:
    return ExternalPurchaseOrder(
        external_order_id=no,
        platform=platform,
        supplier_name="合并测试供应商",
        order_amount=Decimal(amount),
        paid_amount=Decimal(amount),
        purchase_status="confirmed",
    )


def test_external_purchase_order_number_is_scoped_by_platform(db_session):
    """同一个数字订单号允许分别存在于 1688 / 淘宝，不能跨渠道串单。"""
    db_session.add_all([
        _po("SAME-NO-001", "10", "1688"),
        _po("SAME-NO-001", "20", "taobao"),
    ])
    db_session.flush()
    rows = db_session.query(ExternalPurchaseOrder).filter_by(external_order_id="SAME-NO-001").all()
    assert {(row.platform, row.paid_amount) for row in rows} == {
        ("1688", Decimal("10")),
        ("taobao", Decimal("20")),
    }


def test_merge_group_rejects_unbalanced_total_without_partial_links(db_session):
    first = _po("MERGE-BAD-A", "300")
    second = _po("MERGE-BAD-B", "200")
    jpo = JackyunPurchaseOrder(
        jackyun_purch_id="JY-MERGE-BAD",
        purch_no="CG-MERGE-BAD",
        supplier_name="合并测试供应商",
        amount=Decimal("500"),
    )
    db_session.add_all([first, second, jpo])
    db_session.flush()

    with pytest.raises(ValueError, match="不一致"):
        merge_orders_to_jackyun_po(
            db_session,
            purch_no=jpo.purch_no,
            allocations=[
                {"po_id": first.id, "alloc_amount": "300"},
                {"po_id": second.id, "alloc_amount": "190"},
            ],
        )

    assert db_session.query(JackyunPurchaseOrderLink).filter_by(jackyun_po_id=jpo.id).count() == 0
    assert first.purchase_status == "confirmed"
    assert second.purchase_status == "confirmed"


def test_merge_group_is_atomic_and_balanced(db_session):
    first = _po("MERGE-OK-A", "300")
    second = _po("MERGE-OK-B", "200", "taobao")
    jpo = JackyunPurchaseOrder(
        jackyun_purch_id="JY-MERGE-OK",
        purch_no="CG-MERGE-OK",
        supplier_name="合并测试供应商",
        amount=Decimal("500"),
    )
    db_session.add_all([first, second, jpo])
    db_session.flush()

    result = merge_orders_to_jackyun_po(
        db_session,
        purch_no=jpo.purch_no,
        allocations=[
            {"po_id": first.id, "alloc_amount": "300"},
            {"po_id": second.id, "alloc_amount": "200"},
        ],
        actor="pytest",
    )

    assert result["balanced"] is True
    assert result["orderCount"] == 2
    assert Decimal(result["difference"]) == 0
    links = db_session.query(JackyunPurchaseOrderLink).filter_by(jackyun_po_id=jpo.id).all()
    assert {link.po_id: link.alloc_amount for link in links} == {
        first.id: Decimal("300"),
        second.id: Decimal("200"),
    }
    assert all(link.relation_kind == "merged" for link in links)
    assert first.purchase_status == "jackyun_linked"
    assert second.purchase_status == "jackyun_linked"
    assert jackyun_group_summary(db_session, jpo.id)["balanced"] is True


def test_shared_inbound_line_can_be_split_with_quantity_ceiling(db_session):
    sku = ProductSku(
        jackyun_sku_id="JY-SHARED-SKU",
        sku_code="SHARED-SKU",
        sku_name="共用入库 SKU",
    )
    first = _po("SHARED-A", "400")
    second = _po("SHARED-B", "600")
    jpo = JackyunPurchaseOrder(
        jackyun_purch_id="JY-SHARED-PO",
        purch_no="CG-SHARED-PO",
        supplier_name="合并测试供应商",
        amount=Decimal("1000"),
    )
    document = JackyunGoodsDocument(
        document_type="inbound",
        goodsdoc_no="RK-SHARED-001",
        total_amount=Decimal("1000"),
    )
    db_session.add_all([sku, first, second, jpo, document])
    db_session.flush()
    item = JackyunGoodsDocumentItem(
        document_id=document.id,
        line_no=1,
        goods_no="SHARED-SKU",
        goods_name="共用入库 SKU",
        quantity=Decimal("100"),
        unit_price_tax=Decimal("10"),
        amount_tax=Decimal("1000"),
        matched_sku_id=sku.id,
        match_status="manual",
    )
    db_session.add(item)
    db_session.flush()
    first_alloc = PurchaseAllocationItem(
        po_id=first.id,
        sku_id=sku.id,
        sku_code=sku.sku_code,
        goods_name=sku.sku_name,
        quantity=Decimal("40"),
        unit_price=Decimal("10"),
        amount=Decimal("400"),
    )
    second_alloc = PurchaseAllocationItem(
        po_id=second.id,
        sku_id=sku.id,
        sku_code=sku.sku_code,
        goods_name=sku.sku_name,
        quantity=Decimal("60"),
        unit_price=Decimal("10"),
        amount=Decimal("600"),
    )
    db_session.add_all([first_alloc, second_alloc])
    db_session.flush()
    merge_orders_to_jackyun_po(
        db_session,
        purch_no=jpo.purch_no,
        allocations=[
            {"po_id": first.id, "alloc_amount": "400"},
            {"po_id": second.id, "alloc_amount": "600"},
        ],
        actor="pytest",
    )

    result = assign_shared_inbound_item(
        db_session,
        source_item_id=item.id,
        assignments=[
            {"po_id": first.id, "allocation_id": first_alloc.id, "quantity": "40"},
            {"po_id": second.id, "allocation_id": second_alloc.id, "quantity": "60"},
        ],
        actor="pytest",
    )
    assert Decimal(result["allocatedQuantity"]) == Decimal("100")
    assert Decimal(result["remainingQuantity"]) == 0
    assert first_alloc.source_item_id == item.id
    assert second_alloc.source_item_id == item.id
    assert first_alloc.source == "inbound_split"
    assert second_alloc.source == "inbound_split"

    # 同一真实入库行已全部分完，不能再静默超分。
    extra = _po("SHARED-C", "10")
    db_session.add(extra)
    db_session.flush()
    db_session.add(JackyunPurchaseOrderLink(
        po_id=extra.id,
        jackyun_po_id=jpo.id,
        relation_kind="merged",
        alloc_amount=Decimal("10"),
    ))
    extra_alloc = PurchaseAllocationItem(
        po_id=extra.id,
        sku_id=sku.id,
        sku_code=sku.sku_code,
        goods_name=sku.sku_name,
        quantity=Decimal("1"),
        unit_price=Decimal("10"),
        amount=Decimal("10"),
    )
    db_session.add(extra_alloc)
    db_session.flush()
    with pytest.raises(ValueError, match="超过吉客云实际入库数量"):
        assign_shared_inbound_item(
            db_session,
            source_item_id=item.id,
            assignments=[
                {"po_id": first.id, "allocation_id": first_alloc.id, "quantity": "40"},
                {"po_id": extra.id, "allocation_id": extra_alloc.id, "quantity": "1"},
            ],
            actor="pytest",
        )


def test_jackyun_target_invoice_is_apportioned_back_to_merged_source_orders(db_session):
    first = _po("INV-GROUP-A", "300")
    second = _po("INV-GROUP-B", "200", "pdd")
    jpo = JackyunPurchaseOrder(
        jackyun_purch_id="JY-INV-GROUP",
        purch_no="CG-INV-GROUP",
        supplier_name="合并测试供应商",
        amount=Decimal("500"),
    )
    db_session.add_all([first, second, jpo])
    db_session.flush()
    merge_orders_to_jackyun_po(
        db_session,
        purch_no=jpo.purch_no,
        allocations=[
            {"po_id": first.id, "alloc_amount": "300"},
            {"po_id": second.id, "alloc_amount": "200"},
        ],
        actor="pytest",
    )
    invoice = TaxInvoice(
        invoice_key="|INV-GROUP-001",
        invoice_number="INV-GROUP-001",
        direction="input",
        status="issued",
        total_amount=Decimal("500"),
        seller_name="合并测试供应商",
        verified=True,
    )
    db_session.add(invoice)
    db_session.flush()
    db_session.add(TaxInvoiceLink(
        invoice_id=invoice.id,
        target_type="jackyun_purchase_order",
        target_id=jpo.id,
        allocated_amount=Decimal("500"),
        match_method="source_ref",
        confidence=Decimal("1"),
        confirmed=True,
    ))
    db_session.flush()

    first_snapshot = completion_snapshot(db_session, first)
    second_snapshot = completion_snapshot(db_session, second)
    assert Decimal(first_snapshot["invoiceAmount"]) == Decimal("300")
    assert Decimal(first_snapshot["verifiedAmount"]) == Decimal("300")
    assert first_snapshot["invoiceClosed"] is True
    assert first_snapshot["verifiedClosed"] is True
    assert Decimal(second_snapshot["invoiceAmount"]) == Decimal("200")
    assert Decimal(second_snapshot["verifiedAmount"]) == Decimal("200")
    assert second_snapshot["invoiceClosed"] is True


def test_completion_rejects_existence_only_without_full_coverage(db_session):
    po = _po("NOT-CLOSED-001", "100")
    po.purchase_status = "inbound"
    db_session.add(po)
    db_session.flush()
    snapshot = completion_snapshot(db_session, po)
    assert snapshot["complete"] is False
    assert snapshot["inboundClosed"] is False
    assert snapshot["invoiceClosed"] is False
    assert snapshot["paymentClosed"] is False
    assert snapshot["verifiedClosed"] is False
    assert snapshot["issues"]
