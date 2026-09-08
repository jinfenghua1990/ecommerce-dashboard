"""税务系统官方清单导入与发票台账服务。"""

from __future__ import annotations

import hashlib
import mimetypes
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.adapters.bank_file import sanitize_name
from app.adapters.tax_invoice_file import ParsedTaxInvoiceExport, parse_tax_invoice_export
from app.config import settings
from app.core.audit import audit
from app.models.purchase import ExternalPurchaseOrder, JackyunPurchaseOrder, JackyunPurchaseOrderLink
from app.models.sales import SalesOrder
from app.models.tax import TaxInvoice, TaxInvoiceImport, TaxInvoiceImportRecord, TaxInvoiceLink
from app.services.import_lifecycle import (
    filter_active_import,
    filter_lifecycle,
    transition_lifecycle,
    transition_row_status,
)
from sqlalchemy import and_, or_
from sqlalchemy.orm import aliased


def _root() -> Path:
    root = Path(settings.DATA_DIR).resolve() / "tax-invoices"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _store_new_file(content: bytes, original_name: str, sha256: str) -> tuple[Path, bool]:
    target = _root() / sha256[:2] / f"{sha256}_{sanitize_name(original_name)}"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("xb") as output:
            output.write(content)
        return target, True
    except FileExistsError:
        return target, False


def _value(row: dict[str, str], parsed: ParsedTaxInvoiceExport, field: str) -> str:
    header = parsed.mapping.get(field)
    value = str(row.get(header, "") or "").strip() if header else ""
    if value or field != "invoice_number":
        return value
    # 税务数字发票导出常把“发票号码”列留空，把实际号码放在“数电发票号码”。
    for fallback in ("数电发票号码", "电子发票号码", "发票号", "发票编号"):
        value = str(row.get(fallback, "") or "").strip()
        if value:
            return value
    return ""


def _decimal(value: str) -> Decimal | None:
    text = str(value or "").strip().replace(",", "").replace("，", "")
    text = text.replace("¥", "").replace("￥", "").replace("元", "").replace(" ", "")
    if not text or text in {"-", "--", "/"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return -amount if negative else amount


def _datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for candidate in (text, text.replace("/", "-")):
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo(settings.TZ))
        except ValueError:
            pass
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(candidate, pattern).replace(tzinfo=ZoneInfo(settings.TZ))
            except ValueError:
                continue
    return None


def _direction(value: str, header_hint: str) -> str:
    text = f"{value} {header_hint}".lower()
    if any(token in text for token in ("进项", "购进", "收票", "供应商")):
        return "input"
    if any(token in text for token in ("销项", "销售", "开给", "客户")):
        return "output"
    return "unknown"


def _infer_batch_directions(
    rows: list[dict[str, str]], parsed: ParsedTaxInvoiceExport
) -> dict[int, str]:
    """从单批次购销方税号结构补足“进销项”未提供方向的官方清单。

    仅在一侧税号占比至少 80%、另一侧明显变化时推断；不满足条件就保持 unknown。
    这样可识别同一批次的进项/销项文件，但不会把混合批次强行归类。
    """
    seller_counts = Counter(
        _value(row, parsed, "seller_tax_id")
        for row in rows
        if _value(row, parsed, "seller_tax_id")
    )
    buyer_counts = Counter(
        _value(row, parsed, "buyer_tax_id")
        for row in rows
        if _value(row, parsed, "buyer_tax_id")
    )
    total = len(rows)
    if total < 2 or not seller_counts or not buyer_counts:
        return {}
    seller_tax, seller_count = seller_counts.most_common(1)[0]
    buyer_tax, buyer_count = buyer_counts.most_common(1)[0]
    seller_ratio = seller_count / total
    buyer_ratio = buyer_count / total
    if buyer_ratio >= 0.8 and (len(seller_counts) > 1 or seller_ratio < 0.8):
        return {index: "input" for index in range(len(rows)) if buyer_tax}
    if seller_ratio >= 0.8 and (len(buyer_counts) > 1 or buyer_ratio < 0.8):
        return {index: "output" for index in range(len(rows)) if seller_tax}
    return {}


def _status(value: str, total: Decimal | None) -> str:
    text = str(value or "").lower()
    if any(token in text for token in ("作废", "无效", "失效")):
        return "void"
    if any(token in text for token in ("红字", "红冲", "冲红")) or (total is not None and total < 0):
        return "red"
    if any(token in text for token in ("正常", "有效", "已开", "开具")):
        return "issued"
    return "unknown"


def _normalize_row(
    row: dict[str, str], parsed: ParsedTaxInvoiceExport, direction_override: str = ""
) -> tuple[dict, str]:
    number = _value(row, parsed, "invoice_number")
    if not number:
        return {}, "缺少发票号码"
    code = _value(row, parsed, "invoice_code")
    amount = _decimal(_value(row, parsed, "amount_excl_tax"))
    tax_amount = _decimal(_value(row, parsed, "tax_amount"))
    total = _decimal(_value(row, parsed, "total_amount"))
    if total is None and amount is not None and tax_amount is not None:
        total = amount + tax_amount
    if amount is None and total is not None and tax_amount is not None:
        amount = total - tax_amount
    issue_date = _datetime(_value(row, parsed, "issue_date"))
    seller_name = _value(row, parsed, "seller_name")
    seller_tax_id = _value(row, parsed, "seller_tax_id")
    buyer_name = _value(row, parsed, "buyer_name")
    buyer_tax_id = _value(row, parsed, "buyer_tax_id")
    if issue_date is None and total is None and not (seller_name or seller_tax_id or buyer_name or buyer_tax_id):
        return {}, "仅有发票号码，缺少日期、金额或购销方信息"
    direction = _direction(_value(row, parsed, "direction"), parsed.direction_hint)
    if direction == "unknown" and direction_override in ("input", "output"):
        direction = direction_override
    return {
        "invoice_key": f"{code}|{number}",
        "direction": direction,
        "invoice_code": code,
        "invoice_number": number,
        "invoice_type": _value(row, parsed, "invoice_type"),
        "status": _status(_value(row, parsed, "status"), total),
        "issue_date": issue_date,
        "seller_name": seller_name,
        "seller_tax_id": seller_tax_id,
        "buyer_name": buyer_name,
        "buyer_tax_id": buyer_tax_id,
        "amount_excl_tax": amount,
        "tax_amount": tax_amount,
        "total_amount": total,
        "currency": _value(row, parsed, "currency") or "CNY",
        "related_order_ref": _value(row, parsed, "related_order_ref"),
    }, ""


def _auto_link(
    db: Session, invoice: TaxInvoice, related_ref: str, direction: str, *, confirmed: bool = True
) -> bool:
    """只按清单明确给出的订单号自动关联，不按金额/名称猜测。"""
    ref = related_ref.strip()
    amount = invoice.total_amount
    if (invoice.status or "").lower() != "issued" or amount is None or amount <= 0:
        return False
    if not ref:
        return False
    if direction not in ("input", "output"):
        invoice.match_status = "needs_review"
        invoice.match_note = "发票进销项方向未确认，待人工核对后再进入业务链路"
        return False
    candidates: list[tuple[str, int]] = []
    if direction == "input":
        external = db.query(ExternalPurchaseOrder).filter_by(external_order_id=ref).first()
        if external:
            candidates.append(("external_purchase_order", external.id))
        jpo = db.query(JackyunPurchaseOrder).filter_by(purch_no=ref).first()
        if jpo:
            po_ids = sorted({
                int(link.po_id)
                for link in db.query(JackyunPurchaseOrderLink).filter_by(jackyun_po_id=jpo.id).all()
            })
            if len(po_ids) == 1:
                candidates.append(("external_purchase_order", po_ids[0]))
            elif len(po_ids) > 1:
                invoice.match_status = "needs_review"
                invoice.match_note = "吉客云采购单关联多个来源采购单，需人工分摊发票金额"
            else:
                invoice.match_status = "needs_review"
                invoice.match_note = "吉客云采购单尚未关联来源采购主单，暂不计入采购开票进度"
    if direction == "output":
        sales = db.query(SalesOrder).filter_by(order_no=ref).first()
        if sales:
            candidates.append(("sales_order", sales.id))
    candidates = list(dict.fromkeys(candidates))
    if len(candidates) != 1:
        if candidates:
            invoice.match_status = "needs_review"
            invoice.match_note = "同一关联单号命中多个业务对象，待人工确认"
        return False
    target_type, target_id = candidates[0]
    exists = (
        db.query(TaxInvoiceLink)
        .filter_by(invoice_id=invoice.id, target_type=target_type, target_id=target_id)
        .first()
    )
    if exists:
        exists.allocated_amount = invoice.total_amount
        exists.match_method = "source_ref"
        exists.confidence = Decimal("1.0000")
        exists.confirmed = confirmed
    else:
        db.add(TaxInvoiceLink(
            invoice_id=invoice.id,
            target_type=target_type,
            target_id=target_id,
            allocated_amount=invoice.total_amount,
            match_method="source_ref",
            confidence=Decimal("1.0000"),
            confirmed=confirmed,
            note="税务官方清单明确提供关联单号",
        ))
    if confirmed:
        invoice.match_status = "matched"
        invoice.match_note = f"按清单关联单号自动匹配：{target_type}"
    else:
        invoice.match_status = "unmatched"
        invoice.match_note = "草稿发票已识别，确认生效后再进入业务链路"
    return True


def _serialize_import(row: TaxInvoiceImport) -> dict:
    return {
        "id": row.id,
        "originalName": row.original_name,
        "size": row.size,
        "sha256": row.sha256[:16],
        "sourceSystem": row.source_system,
        "period": f"{row.period_year:04d}-{row.period_month:02d}" if row.period_year and row.period_month else "",
        "sheetName": row.sheet_name,
        "headers": row.headers or [],
        "mapping": row.mapping or {},
        "status": row.status,
        "lifecycle": row.lifecycle,
        "lifecycleChangedAt": row.lifecycle_changed_at.isoformat() if row.lifecycle_changed_at else None,
        "rowCount": row.row_count,
        "recognizedRowCount": row.recognized_row_count,
        "matchedRowCount": row.matched_row_count,
        "needsReviewCount": row.needs_review_count,
        "errorSummary": row.error_summary or "",
        "uploader": row.uploader,
        "importedAt": row.imported_at.isoformat() if row.imported_at else None,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def serialize_import(row: TaxInvoiceImport) -> dict:
    return _serialize_import(row)


def _serialize_invoice(row: TaxInvoice) -> dict:
    return {
        "id": row.id,
        "direction": row.direction,
        "invoiceCode": row.invoice_code,
        "invoiceNumber": row.invoice_number,
        "invoiceType": row.invoice_type,
        "status": row.status,
        "issueDate": row.issue_date.isoformat() if row.issue_date else None,
        "sellerName": row.seller_name,
        "sellerTaxId": row.seller_tax_id,
        "buyerName": row.buyer_name,
        "buyerTaxId": row.buyer_tax_id,
        "amountExclTax": str(row.amount_excl_tax) if row.amount_excl_tax is not None else None,
        "taxAmount": str(row.tax_amount) if row.tax_amount is not None else None,
        "totalAmount": str(row.total_amount) if row.total_amount is not None else None,
        "currency": row.currency,
        "matchStatus": row.match_status,
        "matchNote": row.match_note,
        "sourceImportId": row.source_import_id,
        "sourceRowIndex": row.source_row_index,
        "verified": bool(row.verified),
        "verifiedMonth": row.verified_month or "",
    }


def serialize_invoice(row: TaxInvoice) -> dict:
    return _serialize_invoice(row)


def _ingest_rows(
    db: Session, batch: TaxInvoiceImport, parsed: ParsedTaxInvoiceExport
) -> tuple[int, int, int]:
    """把解析行写入标准台账；新导入与历史批次重处理共用这一段。"""
    direction_overrides = _infer_batch_directions(parsed.rows, parsed)
    records_by_index = {
        record.row_index: record
        for record in db.query(TaxInvoiceImportRecord).filter_by(import_id=batch.id).all()
    }
    recognized = 0
    matched = 0
    needs_review = 0
    for row_index, raw in enumerate(parsed.rows, start=1):
        normalized, error = _normalize_row(
            raw, parsed, direction_override=direction_overrides.get(row_index - 1, "")
        )
        record = records_by_index.get(row_index)
        if record is None:
            record = TaxInvoiceImportRecord(import_id=batch.id, row_index=row_index)
            db.add(record)
            records_by_index[row_index] = record
        previous_invoice = db.get(TaxInvoice, record.invoice_id) if record.invoice_id else None
        record.payload = raw
        record.recognition_status = "recognized" if not error else "needs_review"
        record.error_summary = error
        if error:
            record.invoice_id = None
            needs_review += 1
            continue

        invoice = db.query(TaxInvoice).filter_by(invoice_key=normalized["invoice_key"]).first()
        if (
            invoice is None
            and previous_invoice is not None
            and previous_invoice.source_import_id == batch.id
            and previous_invoice.source_row_index == row_index
        ):
            # 兼容历史批次：解析规则从空的“发票号码”切换到“数电发票号码”时，
            # 沿用原行已有的台账记录，避免同一行产生第二张可见发票。
            invoice = previous_invoice
            invoice.invoice_key = normalized["invoice_key"]
        if invoice is None:
            invoice = TaxInvoice(
                invoice_key=normalized["invoice_key"],
                invoice_number=normalized["invoice_number"],
            )
            db.add(invoice)
            db.flush()
        for field in (
            "direction", "invoice_code", "invoice_number", "invoice_type", "status", "issue_date",
            "seller_name", "seller_tax_id", "buyer_name", "buyer_tax_id", "amount_excl_tax",
            "tax_amount", "total_amount", "currency",
        ):
            value = normalized[field]
            if value not in (None, ""):
                setattr(invoice, field, value)
        invoice.source_import_id = batch.id
        invoice.source_row_index = row_index
        invoice.source_system = "tax_export"
        invoice.raw = raw
        invalid_for_business = (
            normalized["status"] != "issued"
            or (normalized["total_amount"] is not None and normalized["total_amount"] <= 0)
        )
        if invalid_for_business:
            for link in db.query(TaxInvoiceLink).filter_by(invoice_id=invoice.id).all():
                link.confirmed = False
                link.match_method = "invalid_invoice"
                link.confidence = None
                link.note = "发票状态非有效或金额非正，不参与业务匹配"
            invoice.match_status = "unmatched"
            invoice.match_note = "发票状态非有效或金额非正，不参与业务匹配"
            invoice.verified = False
            invoice.verified_month = ""
            invoice.verified_at = None
        else:
            invoice.match_status = invoice.match_status or "unmatched"
            linked = _auto_link(
                db, invoice, normalized["related_order_ref"], normalized["direction"],
                confirmed=batch.lifecycle == "active",
            )
            if linked and batch.lifecycle == "active":
                matched += 1
            elif linked:
                invoice.match_status = "unmatched"
                invoice.match_note = "草稿发票已识别，确认生效后再进入业务链路"
        record.invoice_id = invoice.id
        recognized += 1

    batch.sheet_name = parsed.sheet_name
    batch.headers = parsed.headers
    batch.mapping = parsed.mapping
    batch.row_count = len(parsed.rows)
    batch.recognized_row_count = recognized
    batch.matched_row_count = matched
    batch.needs_review_count = needs_review
    batch.status = "needs_review" if needs_review else "parsed"
    batch.error_summary = f"{needs_review} 行需要人工核对" if needs_review else ""
    return recognized, matched, needs_review


def import_export(
    db: Session,
    *,
    content: bytes,
    original_name: str,
    actor: str,
    period_year: int = 0,
    period_month: int = 0,
    auto_confirm: bool = False,
) -> tuple[TaxInvoiceImport, bool]:
    if not content:
        raise ValueError("空文件")
    if len(content) > settings.MAX_UPLOAD_BYTES:
        limit = settings.MAX_UPLOAD_BYTES // (1024 * 1024)
        raise ValueError(f"税务发票清单超过单文件上限（{limit} MiB）")
    original_name = original_name or "tax-invoices.xlsx"
    sha256 = hashlib.sha256(content).hexdigest()
    existing = db.query(TaxInvoiceImport).filter_by(sha256=sha256).first()
    if existing:
        return existing, True

    parsed = parse_tax_invoice_export(content, original_name, max_rows=settings.MAX_JACKYUN_IMPORT_ROWS)
    target, created_file = _store_new_file(content, original_name, sha256)
    now = datetime.now(ZoneInfo(settings.TZ))
    batch = TaxInvoiceImport(
        original_name=original_name,
        stored_path=str(target),
        sha256=sha256,
        size=len(content),
        mime=mimetypes.guess_type(original_name)[0] or "application/octet-stream",
        period_year=period_year,
        period_month=period_month,
        sheet_name=parsed.sheet_name,
        headers=parsed.headers,
        mapping=parsed.mapping,
        uploader=actor,
        lifecycle="active" if auto_confirm else "draft",
        lifecycle_changed_at=now,
        imported_at=now,
    )
    db.add(batch)
    db.flush()

    recognized, matched, needs_review = _ingest_rows(db, batch, parsed)
    try:
        db.commit()
    except Exception:
        db.rollback()
        if created_file:
            target.unlink(missing_ok=True)
        raise
    audit(db, actor, "tax.invoice_import.upload", "tax_invoice_imports", batch.id, {
        "rows": batch.row_count,
        "recognized": batch.recognized_row_count,
        "needsReview": batch.needs_review_count,
    })
    return batch, False


def reprocess_import(db: Session, import_id: int, actor: str = "system") -> TaxInvoiceImport:
    """用当前解析规则重处理一个已保存批次，保留原始行和用户行级删除状态。"""
    batch = db.get(TaxInvoiceImport, import_id)
    if batch is None:
        raise LookupError(f"tax_invoice_imports #{import_id} 不存在")
    if batch.lifecycle == "deleted":
        raise ValueError("回收站批次不能重处理，请先恢复")
    path = Path(batch.stored_path)
    if not path.is_file():
        raise ValueError("找不到该导入的原始文件，无法重处理")
    parsed = parse_tax_invoice_export(
        path.read_bytes(), batch.original_name, max_rows=settings.MAX_JACKYUN_IMPORT_ROWS
    )
    recognized, matched, needs_review = _ingest_rows(db, batch, parsed)
    db.commit()
    audit(
        db,
        actor,
        "tax.invoice_import.reprocess",
        "tax_invoice_imports",
        batch.id,
        {"rows": len(parsed.rows), "recognized": recognized, "matched": matched, "needsReview": needs_review},
    )
    return batch


def list_imports(db: Session, lifecycle: str | None = None, limit: int = 50) -> list[dict]:
    query = db.query(TaxInvoiceImport).order_by(TaxInvoiceImport.id.desc())
    query = filter_lifecycle(query, TaxInvoiceImport, lifecycle)
    rows = query.limit(min(max(limit, 1), 200)).all()
    return [_serialize_import(row) for row in rows]


def confirm_import(db: Session, import_id: int, actor: str) -> TaxInvoiceImport:
    return transition_lifecycle(
        db, TaxInvoiceImport, import_id,
        target="active", allowed_from=("draft",),
        actor=actor, audit_action="tax.invoice_import.confirm",
    )


def soft_delete_import(db: Session, import_id: int, actor: str) -> TaxInvoiceImport:
    return transition_lifecycle(
        db, TaxInvoiceImport, import_id,
        target="deleted", allowed_from=("draft", "active"),
        actor=actor, audit_action="tax.invoice_import.soft_delete",
    )


def restore_import(db: Session, import_id: int, actor: str) -> TaxInvoiceImport:
    return transition_lifecycle(
        db, TaxInvoiceImport, import_id,
        target="draft", allowed_from=("deleted",),
        actor=actor, audit_action="tax.invoice_import.restore",
    )


def delete_row(db: Session, import_id: int, row_index: int, actor: str) -> TaxInvoiceImportRecord:
    """明细核对：删除单行（可恢复）；对应发票同步从台账隐藏。"""
    return transition_row_status(
        db, TaxInvoiceImportRecord,
        lookup={"import_id": import_id, "row_index": row_index},
        target="deleted", actor=actor, audit_action="tax.invoice_import.delete_row",
    )


def restore_row(db: Session, import_id: int, row_index: int, actor: str) -> TaxInvoiceImportRecord:
    return transition_row_status(
        db, TaxInvoiceImportRecord,
        lookup={"import_id": import_id, "row_index": row_index},
        target="active", actor=actor, audit_action="tax.invoice_import.restore_row",
    )


def filter_visible_invoices(query):
    """发票台账可见口径 = 导入 active 且来源明细行未被用户删除。

    供本服务与采购链路共用，避免两处口径漂移。
    无来源行（历史遗留 NULL）的发票保留。
    """
    record = aliased(TaxInvoiceImportRecord)
    query = filter_active_import(query, TaxInvoice, TaxInvoiceImport, TaxInvoice.source_import_id)
    return (
        query.outerjoin(
            record,
            and_(
                record.import_id == TaxInvoice.source_import_id,
                record.row_index == TaxInvoice.source_row_index,
            ),
        )
        .filter(or_(record.id.is_(None), record.row_status != "deleted"))
    )


def list_invoices(
    db: Session,
    *,
    direction: str | None = None,
    status: str | None = None,
    match_status: str | None = None,
    verified: bool | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict]:
    query = filter_visible_invoices(db.query(TaxInvoice)).order_by(
        TaxInvoice.issue_date.desc().nullslast(), TaxInvoice.id.desc()
    )
    if direction:
        query = query.filter(TaxInvoice.direction == direction)
    if status:
        query = query.filter(TaxInvoice.status == status)
    if match_status:
        query = query.filter(TaxInvoice.match_status == match_status)
    if verified is not None:
        query = query.filter(TaxInvoice.verified == verified)
    rows = query.limit(min(max(limit, 1), 500)).offset(max(offset, 0)).all()
    return [_serialize_invoice(row) for row in rows]


def summary(db: Session) -> dict:
    rows = filter_visible_invoices(db.query(TaxInvoice)).all()
    out = {
        "total": len(rows),
        "byDirection": {"input": 0, "output": 0, "unknown": 0},
        "byStatus": {"issued": 0, "void": 0, "red": 0, "unknown": 0},
        "byMatchStatus": {"matched": 0, "unmatched": 0, "needs_review": 0},
    }
    for row in rows:
        out["byDirection"][row.direction] = out["byDirection"].get(row.direction, 0) + 1
        out["byStatus"][row.status] = out["byStatus"].get(row.status, 0) + 1
        out["byMatchStatus"][row.match_status] = out["byMatchStatus"].get(row.match_status, 0) + 1
    return out


def list_import_records(db: Session, import_id: int, limit: int = 200) -> list[dict]:
    rows = (
        db.query(TaxInvoiceImportRecord)
        .filter_by(import_id=import_id)
        .order_by(TaxInvoiceImportRecord.row_index)
        .limit(min(max(limit, 1), 1000))
        .all()
    )
    return [{
        "rowIndex": row.row_index,
        "recognitionStatus": row.recognition_status,
        "invoiceId": row.invoice_id,
        "errorSummary": row.error_summary,
        "rowStatus": row.row_status,
        "payload": row.payload,
    } for row in rows]
