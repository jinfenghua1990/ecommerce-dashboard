"""1688 订单备注中的吉客云入库单号识别与精确建链。"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from decimal import Decimal
from typing import Iterable

from sqlalchemy.orm import Session

from app.core.audit import audit
from app.models.alibaba1688_import import Alibaba1688FileImport, Alibaba1688Order
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
from app.models.jky_web import JkyWebStockinItem, JkyWebStockinOrder
from app.models.procurement_chain import ProcurementChainLink


# 只识别有业务前缀的编号，避免把 1688 订单号或备注里的普通数字误当成入库单。
_REFERENCE_RE = re.compile(
    r"(?<![A-Z0-9])(?:RK|CG|PO)\s*[-_:：#]?\s*"
    r"[A-Z0-9][A-Z0-9_-]{5,}(?![A-Z0-9])",
    re.IGNORECASE,
)
_LABELLED_REFERENCE_RE = re.compile(
    r"(?:JKY\s*[-_:：]?\s*(?:IN|RK)|吉客云(?:采购)?|采购(?:入库)?(?:单|单号)?|入库(?:单|单号)?)"
    r"\s*[:：#]?\s*((?:RK|CG|PO)\s*[-_:：#]?\s*[A-Z0-9][A-Z0-9_-]{5,})",
    re.IGNORECASE,
)


def normalize_reference(value: object) -> str:
    """统一全角字符、大小写和分隔符，供编号精确比对。"""
    text = unicodedata.normalize("NFKC", str(value or "")).upper()
    return re.sub(r"[^A-Z0-9]", "", text)


def extract_reference_numbers(remark: str | None) -> list[str]:
    """从自由格式备注提取去重后的候选单号，顺序按备注中的出现位置保留。"""
    text = unicodedata.normalize("NFKC", str(remark or "")).upper()
    matches: list[tuple[int, str]] = []
    for pattern, group in ((_REFERENCE_RE, 0), (_LABELLED_REFERENCE_RE, 1)):
        for match in pattern.finditer(text):
            value = normalize_reference(match.group(group))
            if value:
                matches.append((match.start(), value))
    references: list[str] = []
    for _, value in sorted(matches):
        if value not in references:
            references.append(value)
    return references


def classify_remark_references(
    remark: str | None,
    known_numbers: Iterable[str],
) -> dict[str, list[str]]:
    """纯函数分类候选编号，便于同步前先做可重复测试。"""
    known = {normalize_reference(value): str(value) for value in known_numbers}
    references = extract_reference_numbers(remark)
    matched = [known[value] for value in references if value in known]
    unverified = [value for value in references if value not in known]
    return {
        "references": references,
        "matched": matched,
        "unverified": unverified,
    }


def _materialize_jky_web_stockin(
    db: Session,
    web_order: JkyWebStockinOrder,
) -> JackyunGoodsDocument:
    """把精确命中的 JKY Web 入库缓存补成采购链可消费的正式入库副本。"""
    document = JackyunGoodsDocument(
        document_type="inbound",
        goodsdoc_no=web_order.goodsdoc_no,
        document_at=web_order.in_out_date,
        warehouse_code=web_order.warehouse_id,
        warehouse_name=web_order.warehouse_name,
        company_name=web_order.company_name,
        supplier_name=web_order.supplier_name,
        total_quantity=web_order.total_quantity,
        total_amount=web_order.has_tax_total_amount or web_order.cost_total_amount,
        total_fee=web_order.tax_total_amount,
        raw={
            "source": "jky_web",
            "jkyWebStockinOrderId": web_order.id,
            "docId": web_order.doc_id,
            "payload": web_order.raw or {},
        },
    )
    db.add(document)
    db.flush()
    items = (
        db.query(JkyWebStockinItem)
        .filter_by(doc_id=web_order.doc_id)
        .order_by(JkyWebStockinItem.id)
        .all()
    )
    for line_no, item in enumerate(items, start=1):
        db.add(JackyunGoodsDocumentItem(
            document_id=document.id,
            line_no=line_no,
            goods_no=item.goods_no,
            sku_barcode=item.barcode,
            goods_name=item.goods_name,
            quantity=item.quantity,
            unit_name=item.unit_name,
            spec=item.spec_name,
            unit_price_tax=item.with_tax_price,
            unit_price_notax=item.cost_price,
            amount_tax=item.with_tax_amount,
            amount_notax=item.cost_amount,
            batch_no=item.batch_no,
            production_date=item.production_date,
            expiry_date=item.expiration_date,
            raw={"source": "jky_web", "jkyWebStockinItemId": item.id, "payload": item.raw or {}},
        ))
    db.flush()
    return document


def run_verified_remark_match(
    db: Session,
    *,
    actor: str = "system",
    order_ids: Iterable[int] | None = None,
) -> dict[str, int]:
    """核验 1688 备注中的单号，并为唯一的吉客云入库单自动建确认链。

    正则只负责找候选；只有本地 ``jackyun_goods_documents`` 中存在唯一同号入库单，
    且该入库单没有被其他订单确认占用时，才会写入 confirmed 链路。
    """
    query = db.query(Alibaba1688Order).join(
        Alibaba1688FileImport,
        Alibaba1688FileImport.id == Alibaba1688Order.import_id,
    ).filter(
        Alibaba1688FileImport.lifecycle == "active",
        Alibaba1688Order.row_status != "deleted",
        Alibaba1688Order.order_remark != "",
    )
    if order_ids is not None:
        ids = {int(value) for value in order_ids}
        if not ids:
            return {
                "ordersScanned": 0, "remarksWithReferences": 0, "referencesFound": 0,
                "linked": 0, "upgraded": 0, "alreadyLinked": 0,
                "unverified": 0, "conflicts": 0, "rejected": 0, "allocSeeded": 0,
            }
        query = query.filter(Alibaba1688Order.id.in_(ids))
    orders = query.order_by(Alibaba1688Order.id).all()

    documents = db.query(JackyunGoodsDocument).filter_by(document_type="inbound").all()
    documents_by_reference: dict[str, list[JackyunGoodsDocument]] = defaultdict(list)
    for document in documents:
        reference = normalize_reference(document.goodsdoc_no)
        if reference:
            documents_by_reference[reference].append(document)
    web_orders = db.query(JkyWebStockinOrder).all()
    web_orders_by_reference: dict[str, list[JkyWebStockinOrder]] = defaultdict(list)
    for web_order in web_orders:
        reference = normalize_reference(web_order.goodsdoc_no)
        if reference:
            web_orders_by_reference[reference].append(web_order)

    links = db.query(ProcurementChainLink).filter(
        ProcurementChainLink.target_type == "inbound",
    ).all()
    links_by_order: dict[int, dict[int, ProcurementChainLink]] = defaultdict(dict)
    for link in links:
        if link.order_id is not None:
            links_by_order[link.order_id][link.target_id] = link

    stats = {
        "ordersScanned": len(orders),
        "remarksWithReferences": 0,
        "referencesFound": 0,
        "linked": 0,
        "upgraded": 0,
        "alreadyLinked": 0,
        "unverified": 0,
        "conflicts": 0,
        "rejected": 0,
        "materializedJkyWeb": 0,
        "allocSeeded": 0,
    }
    changed_links: list[ProcurementChainLink] = []

    for order in orders:
        references = extract_reference_numbers(order.order_remark)
        if not references:
            continue
        stats["remarksWithReferences"] += 1
        stats["referencesFound"] += len(references)
        for reference in references:
            matched_documents = documents_by_reference.get(reference, [])
            if not matched_documents:
                web_matches = web_orders_by_reference.get(reference, [])
                if len(web_matches) == 1:
                    document = _materialize_jky_web_stockin(db, web_matches[0])
                    documents_by_reference[reference].append(document)
                    matched_documents = [document]
                    stats["materializedJkyWeb"] += 1
                else:
                    stats["conflicts"] += 1 if len(web_matches) > 1 else 0
                    stats["unverified"] += 1 if not web_matches else 0
                    continue
            if len(matched_documents) != 1:
                stats["conflicts"] += 1
                continue
            document = matched_documents[0]

            occupied = any(
                link.target_id == document.id
                and link.confirmed
                and link.match_method != "rejected"
                and (link.order_id != order.id or link.external_po_id is not None)
                for link in links
            )
            if occupied:
                stats["conflicts"] += 1
                continue

            existing = links_by_order[order.id].get(document.id)
            if existing is not None:
                if existing.match_method == "rejected":
                    stats["rejected"] += 1
                    continue
                if existing.confirmed:
                    stats["alreadyLinked"] += 1
                    continue
                existing.match_method = "remark_exact"
                existing.confidence = Decimal("1")
                existing.confirmed = True
                existing.note = f"1688订单备注自动识别并核验：{document.goodsdoc_no}"
                changed_links.append(existing)
                stats["upgraded"] += 1
                continue

            link = ProcurementChainLink(
                order_id=order.id,
                external_po_id=None,
                target_type="inbound",
                target_id=document.id,
                match_method="remark_exact",
                confidence=Decimal("1"),
                confirmed=True,
                note=f"1688订单备注自动识别并核验：{document.goodsdoc_no}",
            )
            db.add(link)
            links.append(link)
            links_by_order[order.id][document.id] = link
            changed_links.append(link)
            stats["linked"] += 1

    if changed_links:
        db.flush()
        db.commit()
        try:
            from app.services.inbound_allocation_seed import seed_for_link_batch

            seeded = seed_for_link_batch(db, changed_links)
            stats["allocSeeded"] = int(seeded.get("seeded", 0))
        except Exception:  # 关联已成功，SKU 反填失败留给后续工作台处理。
            db.rollback()
    if stats["referencesFound"]:
        audit(
            db,
            actor,
            "purchase.procurement_chain.remark_match",
            "alibaba1688_orders",
            0,
            stats,
        )
        db.commit()
    return stats
