from datetime import datetime, timezone
from decimal import Decimal

from app.models.alibaba1688_import import Alibaba1688FileImport, Alibaba1688Order
from app.models.catalog import ProductSku
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem, JackyunPurchaseSettlement
from app.models.procurement_chain import ProcurementChainLink
from app.models.purchase import (
    ExternalPurchaseOrder,
    JackyunPurchaseOrder,
    JackyunPurchaseOrderLink,
    PurchaseAllocationItem,
    PurchaseInvoice,
)
from app.services import alibaba1688_import_service as import_service
from app.services import procurement_chain_service as service
from app.services import procurement_workbench_service as workbench_service
from app.services import purchase_service


def test_chain_row_merges_file_order_and_purchase_workflow(db_session):
    order_no = "CHAIN-TEST-001"
    file_import = Alibaba1688FileImport(
        original_name="chain-test.xlsx",
        stored_path="/tmp/chain-test.xlsx",
        sha256="chain-test-sha",
        lifecycle="active",
    )
    db_session.add(file_import)
    db_session.flush()
    file_order = Alibaba1688Order(
        external_order_id=order_no,
        seller_company_name="供应商甲",
        actual_payment=Decimal("100"),
        order_status="交易成功",
        raw_payload={"external_order_id": order_no},
        import_id=file_import.id,
    )
    workflow = ExternalPurchaseOrder(
        external_order_id=order_no,
        supplier_name="供应商甲",
        paid_amount=Decimal("100"),
        purchase_status="confirmed",
    )
    db_session.add_all([file_order, workflow])
    db_session.flush()
    db_session.add(PurchaseAllocationItem(
        po_id=workflow.id,
        sku_id=1,
        sku_code="SKU-001",
        goods_name="测试商品",
        quantity=Decimal("2"),
        unit_price=Decimal("50"),
        amount=Decimal("100"),
    ))
    jpo = JackyunPurchaseOrder(
        jackyun_purch_id="JY-CHAIN-001",
        purch_no="CG-CHAIN-001",
        supplier_name="供应商甲",
        amount=Decimal("100"),
        status="已审核",
    )
    db_session.add(jpo)
    db_session.flush()
    db_session.add(JackyunPurchaseOrderLink(po_id=workflow.id, jackyun_po_id=jpo.id))
    db_session.flush()

    result = service.list_chain(db_session, limit=500)
    row = next(item for item in result["items"] if item["orderNo"] == order_no)

    assert result["total"] >= 1
    assert row["fileOrderId"] == file_order.id
    assert row["externalPoId"] == workflow.id
    assert row["purchaseContentComplete"] is True
    assert row["allocations"][0]["skuCode"] == "SKU-001"
    assert row["purchaseOrders"][0]["purchNo"] == "CG-CHAIN-001"

    workbench_rows = workbench_service.list_orders(db_session, page_size=100)
    workbench_row = next(
        item
        for group in workbench_rows["groups"]
        for item in group["items"]
        if item["orderNo"] == order_no
    )
    workbench_detail = workbench_service.workbench(db_session, file_order.id)

    assert workbench_row["orderId"] == file_order.id
    assert workbench_row["externalPoId"] == workflow.id
    assert workbench_row["purchaseStatus"] == "confirmed"
    assert workbench_detail is not None
    assert workbench_detail["order"]["externalPoId"] == workflow.id
    assert workbench_detail["order"]["purchaseStatus"] == "confirmed"


def test_workflow_creation_backfills_sku_from_existing_inbound_link(db_session):
    """入库链早于工作流 PO 时，建立 PO 也应带出已匹配 SKU。"""
    order_no = "CHAIN-BACKFILL-SKU-001"
    file_import = Alibaba1688FileImport(
        original_name="chain-backfill.xlsx", stored_path="/tmp/chain-backfill.xlsx",
        sha256="chain-backfill-sha", lifecycle="active",
    )
    db_session.add(file_import)
    db_session.flush()
    source = Alibaba1688Order(external_order_id=order_no, import_id=file_import.id)
    sku = ProductSku(jackyun_sku_id="JY-BACKFILL-001", sku_code="SKU-BACKFILL-001", sku_name="回填测试 SKU")
    document = JackyunGoodsDocument(document_type="inbound", goodsdoc_no="RK-BACKFILL-001")
    db_session.add_all([source, sku, document])
    db_session.flush()
    db_session.add(JackyunGoodsDocumentItem(
        document_id=document.id, line_no=1, goods_no="SKU-BACKFILL-001",
        goods_name="回填测试 SKU", quantity=Decimal("2"), unit_price_tax=Decimal("10"),
        matched_sku_id=sku.id, match_status="auto",
    ))
    db_session.add(ProcurementChainLink(
        order_id=source.id, target_type="inbound", target_id=document.id,
        confirmed=True, match_method="manual",
    ))
    db_session.flush()

    workflow = purchase_service.create_external_po(
        db_session, external_order_id=order_no, supplier_name="回填供应商",
        order_amount="20", paid_amount="20",
    )

    allocations = db_session.query(PurchaseAllocationItem).filter_by(po_id=workflow.id).all()
    assert workflow.purchase_status == "confirmed"
    assert [(row.sku_id, row.sku_code, row.quantity, row.amount) for row in allocations] == [
        (sku.id, "SKU-BACKFILL-001", Decimal("2"), Decimal("20.0000")),
    ]


def test_active_import_reclaims_source_from_deleted_batch_without_reviving_deleted_rows(db_session):
    old_import = Alibaba1688FileImport(
        original_name="old.xlsx", stored_path="/tmp/old.xlsx", sha256="reclaim-old", lifecycle="deleted"
    )
    current_import = Alibaba1688FileImport(
        original_name="current.xlsx", stored_path="/tmp/current.xlsx", sha256="reclaim-current", lifecycle="active"
    )
    db_session.add_all([old_import, current_import])
    db_session.flush()
    active_order = Alibaba1688Order(
        external_order_id="RECLAIM-ACTIVE", import_id=old_import.id, seller_company_name="供应商甲"
    )
    deleted_order = Alibaba1688Order(
        external_order_id="RECLAIM-DELETED", import_id=old_import.id, row_status="deleted", seller_company_name="供应商乙"
    )
    db_session.add_all([active_order, deleted_order])
    db_session.flush()

    adopted, skipped = import_service._adopt_orders_from_deleted_imports(
        db_session,
        current_import,
        [
            {"external_order_id": "RECLAIM-ACTIVE", "seller_company_name": "供应商甲"},
            {"external_order_id": "RECLAIM-DELETED", "seller_company_name": "供应商乙"},
        ],
    )
    db_session.flush()

    assert adopted == 1
    assert skipped == 1
    assert active_order.import_id == current_import.id
    assert deleted_order.import_id == old_import.id
    assert deleted_order.row_status == "deleted"


def test_chain_hides_workflow_when_its_only_file_source_is_deleted(db_session):
    order_no = "DELETED-SOURCE-ONLY"
    deleted_import = Alibaba1688FileImport(
        original_name="deleted.xlsx", stored_path="/tmp/deleted.xlsx", sha256="deleted-source-only", lifecycle="deleted"
    )
    db_session.add(deleted_import)
    db_session.flush()
    db_session.add_all([
        Alibaba1688Order(external_order_id=order_no, import_id=deleted_import.id, seller_company_name="供应商甲"),
        ExternalPurchaseOrder(external_order_id=order_no, platform="1688", supplier_name="供应商甲"),
    ])
    db_session.flush()

    result = service.list_chain(db_session, limit=500)
    assert order_no not in {item["orderNo"] for item in result["items"]}


def test_supplier_name_alone_never_creates_inbound_link(db_session):
    file_import = Alibaba1688FileImport(
        original_name="manual-match-only.xlsx",
        stored_path="/tmp/manual-match-only.xlsx",
        sha256="manual-match-only-sha",
        lifecycle="active",
    )
    db_session.add(file_import)
    db_session.flush()
    order = Alibaba1688Order(
        external_order_id="MANUAL-MATCH-ONLY-001",
        seller_company_name="只同名不自动关联供应商",
        actual_payment=Decimal("100"),
        order_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        import_id=file_import.id,
    )
    document = JackyunGoodsDocument(
        document_type="inbound",
        goodsdoc_no="RK-MANUAL-MATCH-ONLY-001",
        supplier_name="只同名不自动关联供应商",
        total_amount=Decimal("999"),
        document_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    db_session.add_all([order, document])
    db_session.flush()

    service.ProcurementChainMatcher(db_session).run_match(auto_confirm=False)

    assert db_session.query(ProcurementChainLink).filter_by(
        order_id=order.id, target_type="inbound", target_id=document.id
    ).first() is None


def test_safe_match_creates_unconfirmed_suggestion(db_session):
    file_import = Alibaba1688FileImport(
        original_name="safe-suggestion.xlsx",
        stored_path="/tmp/safe-suggestion.xlsx",
        sha256="safe-suggestion-sha",
        lifecycle="active",
    )
    db_session.add(file_import)
    db_session.flush()
    order = Alibaba1688Order(
        external_order_id="SAFE-SUGGESTION-001",
        seller_company_name="保守候选供应商",
        actual_payment=Decimal("123.45"),
        order_time=datetime(2026, 2, 1, tzinfo=timezone.utc),
        import_id=file_import.id,
    )
    document = JackyunGoodsDocument(
        document_type="inbound",
        goodsdoc_no="RK-SAFE-SUGGESTION-001",
        supplier_name="保守候选供应商",
        total_amount=Decimal("123.45"),
        document_at=datetime(2026, 2, 3, tzinfo=timezone.utc),
    )
    db_session.add_all([order, document])
    db_session.flush()

    service.ProcurementChainMatcher(db_session).run_match(auto_confirm=False)
    link = db_session.query(ProcurementChainLink).filter_by(
        order_id=order.id, target_type="inbound", target_id=document.id
    ).one()

    assert link.confirmed is False
    assert link.match_method == "auto"
    assert "人工确认" in link.note


def test_manual_inbound_link_can_be_replaced_and_removed(client, db_session):
    file_import = Alibaba1688FileImport(
        original_name="replace-link.xlsx",
        stored_path="/tmp/replace-link.xlsx",
        sha256="replace-link-sha",
        lifecycle="active",
    )
    db_session.add(file_import)
    db_session.flush()
    order = Alibaba1688Order(
        external_order_id="REPLACE-LINK-001",
        seller_company_name="人工更换供应商",
        actual_payment=Decimal("88"),
        order_time=datetime(2026, 3, 1, tzinfo=timezone.utc),
        import_id=file_import.id,
    )
    first = JackyunGoodsDocument(
        document_type="inbound", goodsdoc_no="RK-REPLACE-FIRST", supplier_name="人工更换供应商"
    )
    second = JackyunGoodsDocument(
        document_type="inbound", goodsdoc_no="RK-REPLACE-SECOND", supplier_name="人工更换供应商"
    )
    db_session.add_all([order, first, second])
    db_session.flush()

    created = client.post("/api/v1/procurement-chain/links", json={
        "order_id": order.id,
        "target_type": "inbound",
        "target_id": first.id,
        "note": "测试人工关联",
        "consumable_usage_enabled": False,
    })
    assert created.status_code == 200
    link_id = created.json()["id"]

    replaced = client.put(f"/api/v1/procurement-chain/links/{link_id}", json={
        "target_id": second.id,
        "note": "测试人工更换",
        "consumable_usage_enabled": False,
    })
    assert replaced.status_code == 200
    link = db_session.get(ProcurementChainLink, replaced.json()["id"])
    assert link is not None
    assert link.target_id == second.id
    assert link.confirmed is True
    assert link.match_method == "manual"

    candidates = client.get("/api/v1/procurement-chain/candidates", params={
        "order_id": order.id,
        "target_type": "inbound",
        "q": "RK-REPLACE",
    })
    assert candidates.status_code == 200
    selected = next(item for item in candidates.json()["items"] if item["targetId"] == second.id)
    assert selected["currentlyLinked"] is True
    assert selected["linkId"] == link.id

    removed = client.delete(f"/api/v1/procurement-chain/links/{link.id}")
    assert removed.status_code == 200
    rejected = db_session.get(ProcurementChainLink, link.id)
    assert rejected is not None
    assert rejected.confirmed is False
    assert rejected.match_method == "rejected"

    pending = client.get("/api/v1/procurement-chain/pending")
    assert pending.status_code == 200
    assert link.id not in {item["linkId"] for item in pending.json()["items"]}


def test_workflow_only_order_can_link_a_real_settlement_without_id_collision(client, db_session):
    po = ExternalPurchaseOrder(
        external_order_id="MANUAL-WORKFLOW-SETTLEMENT-001", supplier_name="手工订单供应商",
        paid_amount=Decimal("88"), purchase_status="pending_refine",
    )
    settlement = JackyunPurchaseSettlement(
        settlement_no="SETTLE-WORKFLOW-001", supplier_name="手工订单供应商",
        settlement_amount=Decimal("88"), paid=Decimal("88"), status="已付款",
    )
    db_session.add_all([po, settlement])
    db_session.flush()

    created = client.post("/api/v1/procurement-chain/links", json={
        "order_id": -po.id, "target_type": "settlement", "target_id": settlement.id,
        "note": "手工订单关联真实结算单",
    })
    assert created.status_code == 200
    link = db_session.get(ProcurementChainLink, created.json()["id"])
    assert link is not None
    assert link.order_id is None
    assert link.external_po_id == po.id

    detail = workbench_service.workbench(db_session, -po.id)
    assert detail is not None
    assert detail["order"]["orderId"] == -po.id
    assert detail["detail"]["settlement"][0]["settlementNo"] == settlement.settlement_no


def test_register_invoice_is_atomic_and_rejects_missing_invoice_no(client, db_session):
    po = ExternalPurchaseOrder(
        external_order_id="ATOMIC-INVOICE-001", supplier_name="开票供应商",
        paid_amount=Decimal("100"), purchase_status="pending_refine",
    )
    db_session.add(po)
    db_session.flush()
    baseline = db_session.query(PurchaseInvoice).count()

    failed = client.post(f"/api/v1/purchase/orders/{po.id}/invoices", json={
        "invoice_no": "", "invoice_amount": "100",
    })
    assert failed.status_code == 400
    assert db_session.query(PurchaseInvoice).count() == baseline

    created = client.post(f"/api/v1/purchase/orders/{po.id}/invoices", json={
        "invoice_no": "INV-ATOMIC-001", "invoice_amount": "100", "allocated_amount": "100",
    })
    assert created.status_code == 200
    detail = client.get(f"/api/v1/purchase/orders/{po.id}")
    assert detail.status_code == 200
    assert detail.json()["invoices"][0]["invoiceNo"] == "INV-ATOMIC-001"
