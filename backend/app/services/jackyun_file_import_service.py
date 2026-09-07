"""吉客云客户端官方导出文件的本地受管导入。"""
from __future__ import annotations

import hashlib
import mimetypes
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.bank_file import sanitize_name
from app.adapters.jackyun_export_file import ParsedJackyunExport, parse_jackyun_export
from app.config import settings
from app.core.audit import audit
from app.models.jackyun_import import JackyunFileImport, JackyunFileImportRecord
from app.services.import_lifecycle import filter_lifecycle, transition_lifecycle, transition_row_status


def _root() -> Path:
    root = Path(settings.DATA_DIR).resolve() / "jackyun-exports"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _store_new_file(content: bytes, original_name: str, sha256: str) -> tuple[Path, bool]:
    clean_name = sanitize_name(original_name)
    target = _root() / sha256[:2] / f"{sha256}_{clean_name}"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("xb") as output:
            output.write(content)
        created = True
    except FileExistsError:
        created = False
    return target, created


def _serialize(row: JackyunFileImport) -> dict:
    return {
        "id": row.id,
        "originalName": row.original_name,
        "size": row.size,
        "sha256": row.sha256[:16],
        "reportType": row.report_type,
        "status": row.status,
        "lifecycle": row.lifecycle,
        "lifecycleChangedAt": row.lifecycle_changed_at.isoformat() if row.lifecycle_changed_at else None,
        "sheetName": row.sheet_name,
        "headers": row.headers or [],
        "rowCount": row.row_count,
        "stagedRowCount": row.staged_row_count,
        "errorSummary": row.error_summary or "",
        "uploader": row.uploader,
        "parsedAt": row.parsed_at.isoformat() if row.parsed_at else None,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def import_export(
    db: Session,
    *,
    content: bytes,
    original_name: str,
    actor: str,
    auto_confirm: bool = False,
) -> tuple[JackyunFileImport, bool]:
    """保存原件并将每一行落入受管 staging 表；相同文件指纹不重复导入。

    默认进入 ``draft`` 暂存；``auto_confirm=True`` 时直接 ``active``，用于自动化场景。
    """
    if not original_name:
        original_name = "jackyun-export.xlsx"
    if not content:
        raise ValueError("空文件")
    if len(content) > settings.MAX_JACKYUN_IMPORT_BYTES:
        limit = settings.MAX_JACKYUN_IMPORT_BYTES // (1024 * 1024)
        raise ValueError(f"吉客云导出文件超过单文件上限（{limit} MiB）")

    sha256 = hashlib.sha256(content).hexdigest()
    existing = db.query(JackyunFileImport).filter_by(sha256=sha256).first()
    if existing:
        desired = "active" if auto_confirm else "draft"
        if existing.lifecycle != desired:
            existing.lifecycle = desired
            existing.lifecycle_changed_at = datetime.now(timezone.utc)
            db.commit()
        return existing, True

    parsed: ParsedJackyunExport = parse_jackyun_export(
        content, original_name, max_rows=settings.MAX_JACKYUN_IMPORT_ROWS
    )
    target, created_file = _store_new_file(content, original_name, sha256)
    now = datetime.now(timezone.utc)
    row = JackyunFileImport(
        original_name=original_name,
        stored_path=str(target),
        sha256=sha256,
        size=len(content),
        mime=mimetypes.guess_type(original_name)[0] or "application/octet-stream",
        report_type=parsed.report_type,
        status=parsed.status,
        sheet_name=parsed.sheet_name,
        headers=parsed.headers,
        row_count=len(parsed.rows),
        staged_row_count=len(parsed.rows),
        uploader=actor,
        lifecycle="active" if auto_confirm else "draft",
        lifecycle_changed_at=now,
        parsed_at=now,
    )
    try:
        db.add(row)
        db.flush()
        for offset in range(0, len(parsed.rows), 1000):
            records = [
                JackyunFileImportRecord(import_id=row.id, row_index=index, payload=payload)
                for index, payload in enumerate(parsed.rows[offset:offset + 1000], start=offset + 1)
            ]
            db.add_all(records)
            db.flush()
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(JackyunFileImport).filter_by(sha256=sha256).first()
        if existing:
            return existing, True
        if created_file:
            target.unlink(missing_ok=True)
        raise
    except Exception:
        db.rollback()
        if created_file:
            target.unlink(missing_ok=True)
        raise

    audit(
        db, actor, "jackyun.file_import.upload", "jackyun_file_imports", row.id,
        {"reportType": row.report_type, "rows": row.row_count, "sha256": row.sha256[:16]},
    )
    return row, False


def list_imports(db: Session, lifecycle: str | None = None, limit: int = 50) -> list[dict]:
    query = db.query(JackyunFileImport).order_by(JackyunFileImport.id.desc())
    query = filter_lifecycle(query, JackyunFileImport, lifecycle)
    rows = query.limit(min(max(limit, 1), 200)).all()
    return [_serialize(row) for row in rows]


def serialize_import(row: JackyunFileImport) -> dict:
    return _serialize(row)


def confirm_import(db: Session, import_id: int, actor: str) -> JackyunFileImport:
    return transition_lifecycle(
        db, JackyunFileImport, import_id,
        target="active", allowed_from=("draft",),
        actor=actor, audit_action="jackyun.file_import.confirm",
    )


def soft_delete_import(db: Session, import_id: int, actor: str) -> JackyunFileImport:
    return transition_lifecycle(
        db, JackyunFileImport, import_id,
        target="deleted", allowed_from=("draft", "active"),
        actor=actor, audit_action="jackyun.file_import.soft_delete",
    )


def restore_import(db: Session, import_id: int, actor: str) -> JackyunFileImport:
    return transition_lifecycle(
        db, JackyunFileImport, import_id,
        target="draft", allowed_from=("deleted",),
        actor=actor, audit_action="jackyun.file_import.restore",
    )


def delete_row(db: Session, import_id: int, row_index: int, actor: str) -> JackyunFileImportRecord:
    """明细核对：删除单行原始记录（可恢复）。"""
    return transition_row_status(
        db, JackyunFileImportRecord,
        lookup={"import_id": import_id, "row_index": row_index},
        target="deleted", actor=actor, audit_action="jackyun.file_import.delete_row",
    )


def restore_row(db: Session, import_id: int, row_index: int, actor: str) -> JackyunFileImportRecord:
    return transition_row_status(
        db, JackyunFileImportRecord,
        lookup={"import_id": import_id, "row_index": row_index},
        target="active", actor=actor, audit_action="jackyun.file_import.restore_row",
    )


def list_records(db: Session, import_id: int, limit: int = 200) -> list[dict]:
    """返回导入的逐行原始记录（payload），供「待确认」预览核对表头与样例数据。

    已删除行也返回（带 ``rowStatus``），前端置灰展示并提供恢复入口。
    """
    rows = (
        db.query(JackyunFileImportRecord)
        .filter_by(import_id=import_id)
        .order_by(JackyunFileImportRecord.row_index)
        .limit(min(max(limit, 1), 1000))
        .all()
    )
    return [
        {"rowIndex": r.row_index, "payload": r.payload, "rowStatus": r.row_status}
        for r in rows
    ]


# ---------- 采购报表 → JackyunPurchaseOrder 映射 ----------

_PO_NO_COLS = ("采购单号", "采购订单号", "采购单编号", "单据编号", "订单编号")
_PO_SUPPLIER_COLS = ("供应商", "供应商名称", "往来单位", "供货单位", "供方")
_PO_AMOUNT_COLS = ("价税合计", "价税合计金额", "含税金额", "含税合计", "金额", "总金额", "应付金额")
_PO_DATE_COLS = ("采购日期", "单据日期", "下单日期", "制单日期", "订单日期", "日期")
_PO_STATUS_COLS = ("单据状态", "状态", "采购单状态")
_DETAIL_COLS = ("货品", "商品", "商品名称", "货品名称", "数量", "规格", "单位")

# 入库申请单货品：列名来自吉客云官方导出及已验证历史文件。
# 外部订单引用只接受明确写着 1688/采购订货号的字段，不把泛化“采购订单号”
# 当作外部订单，避免把吉客云内部采购单误建成 ExternalPurchaseOrder。
_INBOUND_APPLY_KEYS = ("申请单号", "入库申请单号", "申请单编号", "入库申请编号")
_INBOUND_ITEM_NO_KEYS = ("货品编号", "货品编码", "商品编号", "商品编码", "SKU编号", "SKU编码")
_INBOUND_BARCODE_KEYS = ("条码", "货品条码", "商品条码", "SKU条码", "条形码", "商品条形码")
_INBOUND_ORDER_REF_KEYS = ("1688采购订单", "1688采购订单号", "1688订单号", "采购订货号", "外部采购订单号")
_INBOUND_AMOUNT_KEYS = ("采购总金额", "价税合计", "含税金额", "入库金额", "金额")


def _pick_cell(row: dict, aliases: tuple[str, ...]) -> str:
    """按别名取原始列值（先精确命中，再大小写不敏感回退）。"""
    for key in aliases:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    lowered = {str(k).lower(): str(k) for k in row}
    for alias in aliases:
        hit = lowered.get(alias.lower())
        if hit and row.get(hit) is not None and str(row[hit]).strip():
            return str(row[hit]).strip()
    return ""


def _to_decimal(text: str) -> Decimal | None:
    cleaned = text.replace(",", "").replace("￥", "").replace("¥", "").replace("元", "").strip()
    if not cleaned or cleaned in ("-", "--", "0.0", "0"):
        return Decimal("0")
    try:
        return Decimal(cleaned)
    except Exception:
        return None


def _serialize_jpo(row) -> dict:
    return {
        "id": row.id,
        "jackyunPurchId": row.jackyun_purch_id,
        "purchNo": row.purch_no or row.jackyun_purch_id,
        "supplierName": row.supplier_name or "",
        "amount": float(row.amount) if row.amount is not None else None,
        "status": row.status or "",
        "raw": row.raw or {},
    }


_DETAIL_AMOUNT_COLS = ("行金额", "本行金额", "明细金额", "金额", "无税金额", "税额")
_HEADER_AMOUNT_COLS = ("价税合计", "价税合计金额", "总金额", "应付金额", "含税合计", "含税金额", "金额")


def _amount_aliases(detail_like: bool) -> tuple[str, ...]:
    return _DETAIL_AMOUNT_COLS if detail_like else _HEADER_AMOUNT_COLS


def map_purchase_import(db: Session, import_id: int, actor: str = "system") -> dict:
    """把已确认（active）的采购报表行映射为 JackyunPurchaseOrder 业务单。"""
    from app.models.purchase import JackyunPurchaseOrder

    imp = db.get(JackyunFileImport, import_id)
    if not imp:
        raise LookupError(f"导入记录 {import_id} 不存在")
    if imp.report_type != "purchase":
        raise ValueError(f"该批次类型为 {imp.report_type or 'unknown'}，仅采购报表可映射")
    if imp.lifecycle != "active":
        raise ValueError("仅 active 批次可映射，请先在面板确认生效")

    records = (
        db.query(JackyunFileImportRecord)
        .filter(JackyunFileImportRecord.import_id == import_id)
        .order_by(JackyunFileImportRecord.row_index)
        .all()
    )
    active_rows = [rec for rec in records if rec.row_status == "active"]
    detail_rows = [
        rec for rec in active_rows
        if _pick_cell(rec.payload, _PO_NO_COLS)
        and any(_pick_cell(rec.payload, (col,)) for col in _DETAIL_COLS)
    ]
    detail_like = len(active_rows) > 0 and len(detail_rows) / len(active_rows) >= 0.5
    amount_aliases = _amount_aliases(detail_like)
    used_headers = {"no": [], "supplier": [], "amount": [], "date": [], "status": [], "detail": []}

    groups: dict[str, list[dict]] = {}
    skipped: list[dict] = []
    for rec in active_rows:
        no = _pick_cell(rec.payload, _PO_NO_COLS)
        if not no:
            skipped.append({"row": rec.row_index, "reason": "缺采购单号列值"})
            continue
        groups.setdefault(no, []).append(rec.payload)

    mapped, updated = 0, 0
    for no, rows in groups.items():
        supplier = next((_pick_cell(r, _PO_SUPPLIER_COLS) for r in rows if _pick_cell(r, _PO_SUPPLIER_COLS)), "")
        status = next((_pick_cell(r, _PO_STATUS_COLS) for r in rows if _pick_cell(r, _PO_STATUS_COLS)), "")
        date_text = next((_pick_cell(r, _PO_DATE_COLS) for r in rows if _pick_cell(r, _PO_DATE_COLS)), "")
        raw_amounts: list[Decimal] = []
        for row in rows:
            text = _pick_cell(row, amount_aliases)
            if text:
                amount = _to_decimal(text)
                if amount is not None:
                    raw_amounts.append(amount)
        if detail_like and len(raw_amounts) > 1 and len({str(a) for a in raw_amounts}) == 1:
            raw_amounts = raw_amounts[:1]
        amount = sum(raw_amounts, Decimal("0")) if raw_amounts else None

        existing = db.query(JackyunPurchaseOrder).filter_by(jackyun_purch_id=no).first()
        raw_meta = {
            "source": "file_import",
            "importId": import_id,
            "rows": len(rows),
            "detailReport": detail_like,
            "date": date_text[:24],
        }
        if existing:
            existing.supplier_name = supplier or existing.supplier_name
            existing.status = status or existing.status
            if amount is not None:
                existing.amount = amount
            existing.raw = {**(existing.raw or {}), **raw_meta}
            updated += 1
        else:
            db.add(JackyunPurchaseOrder(
                jackyun_purch_id=no,
                purch_no=no,
                supplier_name=supplier,
                amount=amount,
                status=status,
                raw=raw_meta,
            ))
            mapped += 1
    db.commit()
    audit(db, actor, "jackyun.file_import.map_purchase", "jackyun_file_imports", import_id,
          {"groups": len(groups), "mapped": mapped, "updated": updated,
           "skipped": len(skipped), "detailReport": detail_like})
    return {
        "ok": True,
        "importId": import_id,
        "detailReport": detail_like,
        "groups": len(groups),
        "mapped": mapped,
        "updated": updated,
        "skipped": skipped,
        "errors": [],
        "usedHeaders": used_headers,
    }


def _parse_date(text: str):
    from datetime import datetime as _dt

    cleaned = (text or "").strip()
    if not cleaned:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S",
                "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return _dt.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def _fill_inbound_item(item, payload: dict) -> list[str]:
    wrote: list[str] = []

    def put(field: str, aliases: tuple[str, ...], *, decimal_field: bool = False):
        value = _pick_cell(payload, aliases)
        if not value:
            return
        if decimal_field:
            parsed = _to_decimal(value)
            if parsed is None:
                return
            setattr(item, field, parsed)
        else:
            setattr(item, field, value[:255])
        wrote.append(field)

    def put_date(field: str, aliases: tuple[str, ...]):
        value = _pick_cell(payload, aliases)
        parsed = _parse_date(value)
        if parsed is None:
            return
        setattr(item, field, parsed)
        wrote.append(field)

    put("spec", ("规格", "规格名称"))
    put("unit_name", ("单位", "基本单位"))
    put("apply_quantity", ("申请数量",), decimal_field=True)
    put("remain_quantity", ("剩余数量",), decimal_field=True)
    put("return_quantity", ("退回数量",), decimal_field=True)
    put("unit_price_tax", ("含税单价",), decimal_field=True)
    put("unit_price_notax", ("无税单价",), decimal_field=True)
    amount_tax = _to_decimal(_pick_cell(payload, ("含税金额", "入库金额")))
    if amount_tax is not None:
        item.amount_tax = amount_tax
        wrote.append("amount_tax")
    amount_notax = _to_decimal(_pick_cell(payload, ("无税金额",)))
    if amount_notax is not None:
        item.amount_notax = amount_notax
        wrote.append("amount_notax")
    put("batch_no", ("批次号",))
    put("production_lot", ("生产批号",))
    put("shelf_life", ("保质期",))
    put("shelf_life_unit", ("保质期单位",))
    put("manufacturer", ("生产厂家",))
    put("approval_no", ("批准文号",))
    put("goods_status", ("货品入库状态", "入库状态"))
    put_date("production_date", ("生产日期",))
    put_date("expiry_date", ("到期日期", "有效期至"))
    if wrote:
        item.raw = {**(item.raw or {}), "fileImportSource": True}
    return wrote


def _infer_purchase_platform(rows: list[dict]) -> str:
    text = " ".join(_pick_cell(row, _PO_SUPPLIER_COLS) for row in rows).lower()
    if "pdd" in text or "拼多多" in text:
        return "pdd"
    if "taobao" in text or "淘宝" in text or "天猫" in text:
        return "taobao"
    if "1688" in text or "阿里" in text:
        return "1688"
    return "other"


def _platform_label(platform: str) -> str:
    return {"pdd": "拼多多", "taobao": "淘宝", "1688": "1688", "other": "其他"}.get(platform, "其他")


def map_inbound_items(db: Session, import_id: int, actor: str = "system") -> dict:
    """把「入库申请单货品」报表回填到入库明细，并登记明确的外部订单关系。"""
    from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem

    imp = db.get(JackyunFileImport, import_id)
    if not imp:
        raise LookupError(f"导入记录 {import_id} 不存在")
    if imp.lifecycle != "active":
        raise ValueError("仅 active 批次可映射，请先在面板确认生效")

    records = [
        rec for rec in db.query(JackyunFileImportRecord)
        .filter(JackyunFileImportRecord.import_id == import_id)
        .order_by(JackyunFileImportRecord.row_index)
        .all()
        if rec.row_status == "active"
    ]
    if not records:
        return {"ok": True, "importId": import_id, "matched": 0, "matchedItems": 0,
                "documents": 0, "skipped": [], "filledFields": 0,
                "sourceRows": 0, "relationshipRows": 0, "uniqueRelationships": 0,
                "createdLinks": 0, "alreadyLinked": 0, "createdExternalOrders": 0,
                "externalOrderNos": [], "missingRk": []}

    probe = records[0].payload
    if not (_pick_cell(probe, _INBOUND_APPLY_KEYS) and _pick_cell(probe, _INBOUND_ITEM_NO_KEYS)):
        raise ValueError("该报表不含「申请单号 + 货品编号」列，无法按入库申请单货品映射")

    groups: dict[str, list[tuple[int, dict]]] = {}
    order_rows: dict[str, list[tuple[int, dict]]] = {}
    relation_rows: dict[tuple[str, str], list[int]] = {}
    skipped: list[dict] = []
    for rec in records:
        no = _pick_cell(rec.payload, _INBOUND_APPLY_KEYS)
        if not no:
            skipped.append({"row": rec.row_index, "reason": "缺申请单号"})
            continue
        groups.setdefault(no, []).append((rec.row_index, rec.payload))
        order_no = _pick_cell(rec.payload, _INBOUND_ORDER_REF_KEYS)
        if order_no:
            order_rows.setdefault(order_no, []).append((rec.row_index, rec.payload))
            relation_rows.setdefault((order_no, no), []).append(rec.row_index)

    matched = 0
    filled_fields = 0
    docs_hit: set[int] = set()
    matched_items: set[int] = set()
    for no, indexed_rows in groups.items():
        doc = (
            db.query(JackyunGoodsDocument)
            .filter(JackyunGoodsDocument.goodsdoc_no == no)
            .order_by(JackyunGoodsDocument.id)
            .first()
        )
        if not doc:
            skipped.append({"row": indexed_rows[0][0], "reason": f"库内无入库单 {no}", "rows": len(indexed_rows)})
            continue
        docs_hit.add(doc.id)
        items = (
            db.query(JackyunGoodsDocumentItem)
            .filter(JackyunGoodsDocumentItem.document_id == doc.id)
            .order_by(JackyunGoodsDocumentItem.line_no)
            .all()
        )
        pool: dict[str, list] = {}
        for item in items:
            for key in (item.goods_no, item.sku_barcode):
                if key:
                    pool.setdefault(str(key).strip(), []).append(item)
        cursor: dict[str, int] = {}
        for row_index, payload in indexed_rows:
            goods_no = _pick_cell(payload, _INBOUND_ITEM_NO_KEYS)
            barcode = _pick_cell(payload, _INBOUND_BARCODE_KEYS)
            target = None
            for key in (goods_no, barcode):
                bucket = pool.get(str(key).strip(), []) if key else []
                if bucket:
                    position = min(cursor.get(str(key).strip(), 0), len(bucket) - 1)
                    target = bucket[position]
                    cursor[str(key).strip()] = cursor.get(str(key).strip(), 0) + 1
                    break
            if target is None:
                skipped.append({"row": row_index, "reason": f"单 {no} 无匹配明细行（货品 {goods_no or barcode}）"})
                continue
            filled = _fill_inbound_item(target, payload)
            if filled:
                filled_fields += len(filled)
                matched += 1
                matched_items.add(target.id)

    from app.models.alibaba1688_import import Alibaba1688Order
    from app.models.procurement_chain import ProcurementChainLink
    from app.models.purchase import ExternalPurchaseOrder
    from app.services.procurement_chain_service import auto_confirm_inbound_link

    order_nos = set(order_rows)
    order_sources = {
        row.external_order_id: row
        for row in db.query(Alibaba1688Order)
        .filter(Alibaba1688Order.external_order_id.in_(order_nos))
        .all()
        if row.row_status != "deleted"
    } if order_nos else {}
    external_orders = {
        row.external_order_id: row
        for row in db.query(ExternalPurchaseOrder)
        .filter(ExternalPurchaseOrder.external_order_id.in_(order_nos))
        .all()
    } if order_nos else {}

    created_external: list[ExternalPurchaseOrder] = []
    for order_no, indexed_rows in order_rows.items():
        if order_no in order_sources:
            continue
        po = external_orders.get(order_no)
        payloads = [payload for _, payload in indexed_rows]
        supplier = next((_pick_cell(row, _PO_SUPPLIER_COLS) for row in payloads if _pick_cell(row, _PO_SUPPLIER_COLS)), "")
        dates = [
            parsed for parsed in (_parse_date(_pick_cell(row, ("创建时间", "采购日期", "下单日期"))) for row in payloads)
            if parsed is not None
        ]
        source_amount = sum(
            (_to_decimal(_pick_cell(row, _INBOUND_AMOUNT_KEYS)) or Decimal("0") for row in payloads),
            Decimal("0"),
        )
        goods_names: list[str] = []
        for payload in payloads:
            goods_name = _pick_cell(payload, ("货品名称", "商品名称", "货品", "商品"))
            if goods_name and goods_name not in goods_names:
                goods_names.append(goods_name)
        platform = _infer_purchase_platform(payloads)
        raw_meta = {
            "source": "jackyun_inbound_apply",
            "sourceImportId": import_id,
            "sourceRowIndexes": [row_index for row_index, _ in indexed_rows],
            "sourceImports": [import_id],
            "channelInferred": platform,
            "inboundAmountTotal": str(source_amount),
            "goodsNames": goods_names[:20],
            "referenceOnly": True,
        }
        if po is None:
            po = ExternalPurchaseOrder(
                external_order_id=order_no,
                platform=platform,
                supplier_name=supplier,
                title=f"{_platform_label(platform)}订单（入库申请单识别）",
                ordered_at=min(dates) if dates else None,
                order_amount=None,
                paid_amount=None,
                raw=raw_meta,
            )
            db.add(po)
            db.flush()
            external_orders[order_no] = po
            created_external.append(po)
        else:
            previous_imports = list((po.raw or {}).get("sourceImports") or [])
            if import_id not in previous_imports:
                previous_imports.append(import_id)
            raw_meta["sourceImports"] = previous_imports
            po.raw = {**(po.raw or {}), **raw_meta}
            if not po.supplier_name and supplier:
                po.supplier_name = supplier
            if po.ordered_at is None and dates:
                po.ordered_at = min(dates)

    existing_links = (
        db.query(ProcurementChainLink)
        .filter(ProcurementChainLink.target_type == "inbound")
        .all()
    )
    existing_by_key: dict[tuple[str, int, int], ProcurementChainLink] = {}
    for link in existing_links:
        if link.order_id is not None:
            existing_by_key[("order", link.order_id, link.target_id)] = link
        elif link.external_po_id is not None:
            existing_by_key[("external", link.external_po_id, link.target_id)] = link
    rk_nos = {rk for _, rk in relation_rows}
    docs_by_no = {
        doc.goodsdoc_no: doc
        for doc in db.query(JackyunGoodsDocument)
        .filter(
            JackyunGoodsDocument.document_type == "inbound",
            JackyunGoodsDocument.goodsdoc_no.in_(rk_nos),
        )
        .all()
    } if rk_nos else {}

    created_links: list[ProcurementChainLink] = []
    auto_confirmed_link_objects: list[ProcurementChainLink] = []
    already_linked = 0
    auto_confirmed_links = 0
    rejected_links = 0
    pending_links = 0
    upgraded_links = 0
    missing_rk: list[str] = []
    for (order_no, rk_no), row_indexes in relation_rows.items():
        doc = docs_by_no.get(rk_no)
        if doc is None:
            missing_rk.append(rk_no)
            skipped.append({"row": row_indexes[0], "reason": f"库内无入库单 {rk_no}", "rows": len(row_indexes)})
            continue
        source_order = order_sources.get(order_no)
        po = external_orders.get(order_no)
        if source_order is not None:
            source_key = ("order", source_order.id, doc.id)
            link_values = {"order_id": source_order.id, "external_po_id": None}
        elif po is not None:
            source_key = ("external", po.id, doc.id)
            link_values = {"order_id": None, "external_po_id": po.id}
        else:
            skipped.append({"row": row_indexes[0], "reason": f"订单号 {order_no} 未建立采购主档"})
            continue
        previous = existing_by_key.get(source_key)
        if previous is not None:
            if previous.match_method == "rejected":
                rejected_links += 1
            else:
                already_linked += 1
                if previous.match_method not in {"manual", "file_import"}:
                    previous.match_method = "file_import"
                    previous.confidence = Decimal("1")
                    previous.note = (
                        f"来源：入库申请单货品导入 #{import_id}"
                        f"（原始行 {len(row_indexes)} 行）；原自动候选已被明确关系覆盖"
                    )
                    upgraded_links += 1
                needs_auto_confirm = (
                    not previous.confirmed
                    or not previous.consumable_usage_decided
                    or previous.consumable_usage_enabled is None
                )
                if needs_auto_confirm:
                    auto_confirm_inbound_link(previous)
                    auto_confirmed_link_objects.append(previous)
                    auto_confirmed_links += 1
                else:
                    pending_links += int(not previous.confirmed)
            continue
        link = ProcurementChainLink(
            **link_values,
            target_type="inbound",
            target_id=doc.id,
            match_method="file_import",
            confidence=Decimal("1"),
            confirmed=True,
            note=f"来源：入库申请单货品导入 #{import_id}（原始行 {len(row_indexes)} 行）",
        )
        auto_confirm_inbound_link(link)
        db.add(link)
        created_links.append(link)
        existing_by_key[source_key] = link

    authoritative_target_ids = {
        link.target_id
        for link in db.query(ProcurementChainLink)
        .filter(
            ProcurementChainLink.target_type == "inbound",
            ProcurementChainLink.target_id.in_({doc.id for doc in docs_by_no.values()}),
            ProcurementChainLink.match_method.in_(("manual", "file_import")),
            ProcurementChainLink.match_method != "rejected",
        )
        .all()
    } if docs_by_no else set()
    reconciled_links = 0
    reconciled_link_ids: list[int] = []
    if authoritative_target_ids:
        from app.services.consumable_service import _reverse_inbound_usage

        heuristic_links = db.query(ProcurementChainLink).filter(
            ProcurementChainLink.target_type == "inbound",
            ProcurementChainLink.target_id.in_(authoritative_target_ids),
            ProcurementChainLink.match_method == "auto",
        ).all()
        for link in heuristic_links:
            if link.consumable_usage_enabled is True:
                _reverse_inbound_usage(db, link.id)
            link.confirmed = False
            link.consumable_usage_decided = False
            link.consumable_usage_enabled = None
            link.match_method = "rejected"
            link.confidence = None
            link.note = (
                f"{link.note}；入库匹配文件 #{import_id} 的明确关系已覆盖自动候选"
                if link.note else f"入库匹配文件 #{import_id} 的明确关系已覆盖自动候选"
            )
            reconciled_links += 1
            reconciled_link_ids.append(link.id)

    db.commit()
    alloc_seeded = 0
    seed_links = created_links[:]
    seed_links.extend(auto_confirmed_link_objects)
    if seed_links:
        from app.services.inbound_allocation_seed import seed_for_link_batch
        try:
            alloc_seeded = int(seed_for_link_batch(db, seed_links).get("seeded", 0))
        except Exception:
            alloc_seeded = 0
    audit(db, actor, "jackyun.file_import.map_inbound", "jackyun_file_imports", import_id,
          {"documents": len(docs_hit), "matched": matched,
           "matchedItems": len(matched_items), "filledFields": filled_fields,
           "sourceRows": len(records), "relationshipRows": sum(len(v) for v in relation_rows.values()),
           "uniqueRelationships": len(relation_rows), "createdLinks": len(created_links),
           "alreadyLinked": already_linked, "upgradedLinks": upgraded_links,
           "reconciledLinks": reconciled_links, "reconciledLinkIds": reconciled_link_ids,
           "autoConfirmedLinks": auto_confirmed_links,
           "createdExternalOrders": len(created_external),
           "allocSeeded": alloc_seeded, "skipped": len(skipped)})
    return {
        "ok": True,
        "importId": import_id,
        "documents": len(docs_hit),
        "matched": matched,
        "matchedItems": len(matched_items),
        "filledFields": filled_fields,
        "sourceRows": len(records),
        "relationshipRows": sum(len(v) for v in relation_rows.values()),
        "uniqueRelationships": len(relation_rows),
        "createdLinks": len(created_links),
        "alreadyLinked": already_linked,
        "upgradedLinks": upgraded_links,
        "reconciledLinks": reconciled_links,
        "reconciledLinkIds": reconciled_link_ids,
        "autoConfirmedLinks": auto_confirmed_links,
        "pendingLinks": pending_links,
        "rejectedLinks": rejected_links,
        "createdExternalOrders": len(created_external),
        "externalOrderNos": [po.external_order_id for po in created_external],
        "allocSeeded": alloc_seeded,
        "missingRk": sorted(set(missing_rk)),
        "skipped": skipped,
    }


def map_import(db: Session, import_id: int, actor: str = "system") -> dict:
    imp = db.get(JackyunFileImport, import_id)
    if not imp:
        raise LookupError(f"导入记录 {import_id} 不存在")
    headers = list(imp.headers or [])
    if imp.lifecycle != "active":
        return {"ok": True, "skipped": True, "reason": "draft 批次，确认后再映射"}

    if _pick_cell({header: header for header in headers}, _PO_NO_COLS):
        return {**map_purchase_import(db, import_id, actor=actor), "mapper": "purchase"}
    has_apply = any(_pick_cell({header: header for header in headers}, (key,)) for key in _INBOUND_APPLY_KEYS)
    has_item = any(_pick_cell({header: header for header in headers}, (key,)) for key in _INBOUND_ITEM_NO_KEYS)
    if has_apply and has_item:
        return {**map_inbound_items(db, import_id, actor=actor), "mapper": "inbound_items"}
    return {"ok": True, "skipped": True, "reason": "无匹配映射器（原始行已存档）"}
