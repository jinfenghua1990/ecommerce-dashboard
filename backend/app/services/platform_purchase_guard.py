"""跨渠道采购订单号隔离兼容层（V1.6.3）。

历史代码在仅支持 1688 时普遍用 external_order_id 单字段回查。V1.6.3 把工作流
幂等键升级为 platform + external_order_id 后，这些桥接查询必须明确：
- Alibaba1688Order 只能桥接 platform=1688 的 ExternalPurchaseOrder；
- 淘宝 / 拼多多 / 其他渠道同号订单必须作为独立工作流行保留；
- 税务清单只给“订单号”但未给渠道且命中多张单时，必须进入人工确认，不能猜。

这里集中安装兼容守卫，避免在大体量历史服务中做高风险整文件重写。
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.alibaba1688_import import Alibaba1688FileImport, Alibaba1688Order
from app.models.purchase import ExternalPurchaseOrder, InboundLink
from app.models.procurement_chain import ProcurementChainLink
from app.services.import_lifecycle import filter_active_import, filter_active_rows


def platform_source_pairs(db: Session) -> list[tuple[Alibaba1688Order | None, ExternalPurchaseOrder | None]]:
    """1688 文件副本只与 platform=1688 工作流副本配对；其他渠道同号单独展示。"""
    file_orders = (
        filter_active_rows(
            filter_active_import(
                db.query(Alibaba1688Order),
                Alibaba1688Order,
                Alibaba1688FileImport,
                Alibaba1688Order.import_id,
            ),
            Alibaba1688Order,
        )
        .order_by(Alibaba1688Order.id.desc())
        .all()
    )
    removed_nos = {
        no
        for (no,) in db.query(Alibaba1688Order.external_order_id).filter(
            Alibaba1688Order.row_status == "deleted"
        ).all()
    }
    source_nos = {no for (no,) in db.query(Alibaba1688Order.external_order_id).all()}
    active_source_nos = {row.external_order_id for row in file_orders}

    workflow_1688_by_no = {
        po.external_order_id: po
        for po in db.query(ExternalPurchaseOrder).filter(
            ExternalPurchaseOrder.platform == "1688"
        ).order_by(ExternalPurchaseOrder.id.desc()).all()
        if po.external_order_id not in removed_nos
        and (po.external_order_id in active_source_nos or po.external_order_id not in source_nos)
    }
    pairs: list[tuple[Alibaba1688Order | None, ExternalPurchaseOrder | None]] = []
    for order in file_orders:
        pairs.append((order, workflow_1688_by_no.pop(order.external_order_id, None)))
    pairs.extend((None, po) for po in workflow_1688_by_no.values())

    # 非 1688 采购订单没有 Alibaba 文件副本，始终作为独立工作流行展示；
    # 即使订单号与 1688 恰好相同，也不能被 removed_nos/source_nos 吞掉。
    for po in db.query(ExternalPurchaseOrder).filter(
        ExternalPurchaseOrder.platform != "1688"
    ).order_by(ExternalPurchaseOrder.id.desc()).all():
        pairs.append((None, po))
    return pairs


def sync_1688_purchase_workflow_order(
    db: Session,
    data: dict[str, Any],
    *,
    source: str = "alibaba1688_file",
) -> bool:
    """1688 导入只 upsert platform=1688 的工作流副本，不覆盖其他渠道同号订单。"""
    from app.services import alibaba1688_import_service as source_svc

    external_order_id = str(data.get("external_order_id") or "").strip()
    if not external_order_id:
        return False
    supplier = str(data.get("seller_company_name") or "").strip()
    buyer = str(data.get("buyer_member_name") or data.get("buyer_company_name") or "").strip()
    order_status = str(data.get("order_status") or "").strip()
    order_remark = str(data.get("order_remark") or "").strip()
    ordered_at = source_svc._to_datetime(data.get("order_time"))
    paid_amount = (
        source_svc._to_decimal(data.get("actual_payment"))
        if data.get("actual_payment") not in (None, "") else None
    )
    goods_total = (
        source_svc._to_decimal(data.get("goods_total"))
        if data.get("goods_total") not in (None, "") else None
    )
    freight = (
        source_svc._to_decimal(data.get("freight"))
        if data.get("freight") not in (None, "") else None
    )
    discount = (
        source_svc._to_decimal(data.get("discount"))
        if data.get("discount") not in (None, "") else None
    )
    order_amount = None
    if goods_total is not None or freight is not None or discount is not None:
        order_amount = (
            (goods_total or Decimal("0"))
            + (freight or Decimal("0"))
            - (discount or Decimal("0"))
        )
    now = datetime.now(timezone.utc)
    raw = {"source": source, **source_svc._json_safe_dict(data)}

    row = db.query(ExternalPurchaseOrder).filter_by(
        platform="1688", external_order_id=external_order_id
    ).first()
    if row is None:
        db.add(ExternalPurchaseOrder(
            external_order_id=external_order_id,
            platform="1688",
            buyer_account=buyer,
            supplier_name=supplier,
            title="",
            ordered_at=ordered_at,
            order_amount=order_amount,
            paid_amount=paid_amount,
            order_status=order_status,
            synced_at=now,
            raw=raw,
        ))
        return True

    changed = False
    if supplier and row.supplier_name != supplier:
        row.supplier_name = supplier
        changed = True
    if buyer and row.buyer_account != buyer:
        row.buyer_account = buyer
        changed = True
    if order_status and row.order_status != order_status:
        row.order_status = order_status
        changed = True
    if order_remark and (row.raw or {}).get("order_remark") != order_remark:
        changed = True
    if ordered_at is not None and row.ordered_at != ordered_at:
        row.ordered_at = ordered_at
        changed = True
    if order_amount is not None and row.order_amount != order_amount:
        row.order_amount = order_amount
        changed = True
    if paid_amount is not None and row.paid_amount != paid_amount:
        row.paid_amount = paid_amount
        changed = True
    merged_raw = {**(row.raw or {}), **raw}
    if row.raw != merged_raw:
        row.raw = merged_raw
        changed = True
    if row.synced_at != now:
        row.synced_at = now
        changed = True
    return changed


def inbound_po_from_link(db: Session, link: ProcurementChainLink) -> ExternalPurchaseOrder | None:
    """1688 链路按 platform=1688 回查工作流 PO；external_po_id 则直接按主键。"""
    if link.target_type != "inbound":
        return None
    if link.external_po_id:
        return db.get(ExternalPurchaseOrder, link.external_po_id)
    if link.order_id:
        order = db.get(Alibaba1688Order, link.order_id)
        if order and order.external_order_id:
            return db.query(ExternalPurchaseOrder).filter_by(
                platform="1688", external_order_id=order.external_order_id
            ).first()
    return None


def collect_inbound_doc_ids_for_po(db: Session, po: ExternalPurchaseOrder) -> set[int]:
    """仅 1688 PO 使用 Alibaba 订单号桥接；其他渠道只走 external_po_id。"""
    doc_ids: set[int] = set()
    if (po.platform or "").lower() == "1688" and po.external_order_id:
        order = db.query(Alibaba1688Order).filter_by(
            external_order_id=po.external_order_id
        ).first()
        if order:
            for link in db.query(ProcurementChainLink).filter_by(
                order_id=order.id,
                target_type="inbound",
                confirmed=True,
            ).all():
                doc_ids.add(link.target_id)
    for link in db.query(ProcurementChainLink).filter_by(
        external_po_id=po.id,
        target_type="inbound",
        confirmed=True,
    ).all():
        doc_ids.add(link.target_id)
    # 兼容旧 InboundLink；历史模型字段名实际为 goodsdoc_no，部分旧服务曾把它当 document_id。
    # 只有能解析成数字主键时才纳入，避免把 RK 单号误当 ID。
    for link in db.query(InboundLink).filter_by(po_id=po.id).all():
        raw = link.raw or {}
        document_id = raw.get("documentId") or raw.get("document_id")
        if document_id is not None:
            try:
                doc_ids.add(int(document_id))
            except (TypeError, ValueError):
                pass
    return doc_ids


def tax_auto_link_platform_safe(db: Session, invoice, related_ref: str, direction: str) -> bool:
    """税务清单仅给订单号时，多渠道同号必须待人工确认，绝不取第一条。"""
    from app.models.purchase import JackyunPurchaseOrder
    from app.models.sales import SalesOrder
    from app.models.tax import TaxInvoiceLink

    ref = related_ref.strip()
    if not ref:
        return False
    candidates: list[tuple[str, int]] = []
    if direction in ("input", "unknown"):
        for external in db.query(ExternalPurchaseOrder).filter_by(
            external_order_id=ref
        ).all():
            candidates.append(("external_purchase_order", external.id))
        jpo = db.query(JackyunPurchaseOrder).filter_by(purch_no=ref).first()
        if jpo:
            candidates.append(("jackyun_purchase_order", jpo.id))
    if direction in ("output", "unknown"):
        sales = db.query(SalesOrder).filter_by(order_no=ref).first()
        if sales:
            candidates.append(("sales_order", sales.id))
    if len(candidates) != 1:
        if candidates:
            invoice.match_status = "needs_review"
            invoice.match_note = "同一关联单号命中多个业务对象或多个采购渠道，待人工确认"
        return False

    target_type, target_id = candidates[0]
    exists = db.query(TaxInvoiceLink).filter_by(
        invoice_id=invoice.id,
        target_type=target_type,
        target_id=target_id,
    ).first()
    if exists:
        exists.allocated_amount = invoice.total_amount
        exists.match_method = "source_ref"
        exists.confidence = Decimal("1.0000")
        exists.confirmed = True
    else:
        db.add(TaxInvoiceLink(
            invoice_id=invoice.id,
            target_type=target_type,
            target_id=target_id,
            allocated_amount=invoice.total_amount,
            match_method="source_ref",
            confidence=Decimal("1.0000"),
            confirmed=True,
            note="税务官方清单明确提供关联单号",
        ))
    invoice.match_status = "matched"
    invoice.match_note = f"按清单关联单号自动匹配：{target_type}"
    return True


_INSTALLED = False


def install_platform_purchase_guards() -> None:
    """安装跨渠道同号隔离；API/服务继续沿用原函数签名。"""
    global _INSTALLED
    if _INSTALLED:
        return

    from app.api.v1 import purchase as purchase_api
    from app.services import (
        alibaba1688_import_service,
        inbound_allocation_seed,
        procurement_board_service,
        procurement_chain_service,
        procurement_workbench_service,
        tax_invoice_service,
    )

    procurement_chain_service._source_pairs = platform_source_pairs
    procurement_workbench_service._source_pairs = platform_source_pairs
    procurement_board_service._source_pairs = platform_source_pairs
    purchase_api._source_pairs = platform_source_pairs

    alibaba1688_import_service._sync_purchase_workflow_order = sync_1688_purchase_workflow_order
    inbound_allocation_seed._po_from_link = inbound_po_from_link
    inbound_allocation_seed.collect_linked_doc_ids_for_po = collect_inbound_doc_ids_for_po
    tax_invoice_service._auto_link = tax_auto_link_platform_safe
    _INSTALLED = True
