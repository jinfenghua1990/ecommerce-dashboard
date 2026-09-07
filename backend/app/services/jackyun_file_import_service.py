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
        # 数据库唯一约束仍是最终幂等保障；同 hash 文件本身无需覆盖。
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
        # 重复上传：deleted 记录恢复可用、draft 在 auto_confirm 下随本次意图转 active，
        # 保证「重新上传 = 重新映射」，不会被旧生命周期卡住。
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
        # 每行用原始中文列名落库；后续只需补充真实映射，不会丢掉本次文件数据。
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
        # 仅移除本次按排他方式创建的文件；绝不碰此前存在的归档。
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
# 明细行判据：报表含货品行 → 视为明细报表，按单号分组后金额列求和为整单金额。
_DETAIL_COLS = ("货品", "商品", "商品名称", "货品名称", "数量", "规格", "单位")


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
    from app.models.purchase import JackyunPurchaseOrder  # noqa: F401 (type hint only)
    return {
        "id": row.id,
        "jackyunPurchId": row.jackyun_purch_id,
        "purchNo": row.purch_no or row.jackyun_purch_id,
        "supplierName": row.supplier_name or "",
        "amount": float(row.amount) if row.amount is not None else None,
        "status": row.status or "",
        "raw": row.raw or {},
    }


# 明细型报表的金额列：行小计（不含整单"价税合计"重复值）。
_DETAIL_AMOUNT_COLS = ("行金额", "本行金额", "明细金额", "金额", "无税金额", "税额")
# 单头型报表的金额列：整单金额。
_HEADER_AMOUNT_COLS = ("价税合计", "价税合计金额", "总金额", "应付金额", "含税合计", "含税金额", "金额")


def _amount_aliases(detail_like: bool) -> tuple[str, ...]:
    return _DETAIL_AMOUNT_COLS if detail_like else _HEADER_AMOUNT_COLS


def map_purchase_import(db: Session, import_id: int, actor: str = "system") -> dict:
    """把已确认（active）的采购报表行映射为 JackyunPurchaseOrder 业务单。

    - 仅处理 report_type == purchase 的导入批次。
    - 形态判定：同一行同时含「单号列」与「货品/明细列」的比例 >= 0.5 → 明细报表，
      按单号分组、金额列取行小计求和为整单金额；否则为单头报表，一行一单。
    - 幂等：jackyun_purch_id = 采购单号，已存在则原地更新，不重复建单。
    """
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
    # 明细行判据：同一行同时有单号 + 明细列；占比过半视为明细报表。
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
        for r in rows:
            text = _pick_cell(r, amount_aliases)
            if text:
                amount = _to_decimal(text)
                if amount is not None:
                    raw_amounts.append(amount)
        if detail_like and len(raw_amounts) > 1 and len({str(a) for a in raw_amounts}) == 1:
            # 明细行整单金额重复出现 → 只取一次，避免求和翻倍。
            raw_amounts = raw_amounts[:1]
        amount = sum(raw_amounts, Decimal("0")) if raw_amounts else None

        existing = (
            db.query(JackyunPurchaseOrder)
            .filter_by(jackyun_purch_id=no)
            .first()
        )
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
    """宽松解析日期/时间文本，失败返回 None。"""
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
    """把一行入库申请单货品写进明细行；返回被写入的字段名（便于统计）。"""
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
    put_amount_tax = _to_decimal(_pick_cell(payload, ("含税金额", "入库金额")))
    if put_amount_tax is not None:
        item.amount_tax = put_amount_tax
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
    """从来源行的往来单位推断非 1688 渠道；不确定时落到 other。"""
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
    """把「入库申请单货品」报表的行回填到已存在的入库单明细上。

    匹配键：申请单号 → JackyunGoodsDocument.goodsdoc_no；货品编号/条码 → 明细行。
    只补齐模型里约定由文件通道写入的维度（单价、申请/剩余数量、批次效期等），
    不覆盖人工锁定的金额校验结论（match_status == manual）。
    """
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

    # 形态判定：必须同时能取到「申请单号」与「货品编号」才认作入库申请单货品
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
            if no:
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
        # 按货品编号（回退条码）建立匹配池。来源文件的每一行都保留；
        # 同一入库明细可能对应多个采购订单，所以重复行要复用同一目标明细，不能因“已用过”而跳过。
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

    # 订单号进入采购链路的统一登记：1688 有原始订单就引用原始订单，
    # 没有原始订单的编号（PDD/淘宝/线下等）建立 workflow 订单主档。
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
                # 入库文件的采购总金额是来源行金额合计，不等同于外部订单实付，
                # 这里仅存到 raw 供核对，避免把推算值当成付款事实。
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
                # 入库匹配文件中的订单号 + 入库单号是明确关系。
                # 旧版本可能已经先按供应商/金额/日期生成了 auto 关系，
                # 这里升级其来源，不覆盖人工关系。
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
            # 文件关系按明确的订单号 + 入库单号直接确认；没有耗材来源信息时明确不使用，
            # 不触发库存扣减。
            confirmed=True,
            note=f"来源：入库申请单货品导入 #{import_id}（原始行 {len(row_indexes)} 行）",
        )
        auto_confirm_inbound_link(link)
        db.add(link)
        created_links.append(link)
        existing_by_key[source_key] = link

    # 文件明确的入库单归属优先于旧的启发式候选。只处理本批次涉及的入库单，
    # 仅撤销 auto 关系，保留 manual/file_import 关系及审计记录，不删除数据。
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
            # 链路已经落库；SKU 反填失败不回滚订单/入库关系，后续可从工作台重试。
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
    """按报表内容自动选择映射器（不依赖类型判定结果）。

    - 含「采购单号」→ 采购单映射器；
    - 含「申请单号 + 货品编号」→ 入库申请单货品映射器；
    - 都不满足 → 返回 skipped，保持原始行存档。
    """
    imp = db.get(JackyunFileImport, import_id)
    if not imp:
        raise LookupError(f"导入记录 {import_id} 不存在")
    headers = list(imp.headers or [])
    if imp.lifecycle != "active":
        return {"ok": True, "skipped": True, "reason": "draft 批次，确认后再映射"}

    if _pick_cell({h: h for h in headers}, _PO_NO_COLS):
        return {**map_purchase_import(db, import_id, actor=actor), "mapper": "purchase"}
    has_apply = any(_pick_cell({h: h for h in headers}, (k,)) for k in _INBOUND_APPLY_KEYS)
    has_item = any(_pick_cell({h: h for h in headers}, (k,)) for k in _INBOUND_ITEM_NO_KEYS)
    if has_apply and has_item:
        return {**map_inbound_items(db, import_id, actor=actor), "mapper": "inbound_items"}
    return {"ok": True, "skipped": True, "reason": "无匹配映射器（原始行已存档）"}
