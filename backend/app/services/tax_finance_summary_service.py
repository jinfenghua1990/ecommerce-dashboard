"""财务交付汇总：底层保留税务明细，发给财务时只按会计大类汇总。

原则：
- 只使用 active 的官方税务导入；金额、税额、数量均不从吉客云/1688/手工数据补齐。
- 汇总维度为“财务大类 + 税率”；不同税率不能混成一行。
- 数量仅在同一汇总组的单位一致时合计；多单位时不强行相加。
- 可读取官方项目名形如 *软饮料*具体商品 的税收分类前缀；除此之外不猜分类。
- 原始 TaxInvoiceImportRecord 永久保留，本服务只读，不修改、不删除明细。
"""
from __future__ import annotations

import csv
import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import settings
from app.models.tax import TaxInvoice, TaxInvoiceImport, TaxInvoiceImportRecord


ALIASES: dict[str, tuple[str, ...]] = {
    "accounting_category": (
        "财务大类", "会计大类", "做账大类", "税收分类名称", "商品和服务税收分类名称",
        "税收分类编码名称", "商品和服务税收分类编码简称",
    ),
    "goods_name": (
        "货物或应税劳务、服务名称", "货物或应税劳务服务名称", "项目名称", "商品名称",
        "货物名称", "服务名称", "品名", "名称",
    ),
    "unit": ("单位", "计量单位"),
    "quantity": ("数量", "开票数量", "商品数量"),
    "unit_price": ("单价", "不含税单价", "单价(不含税)", "单价（不含税）"),
    "amount_excl_tax": ("不含税金额", "金额(不含税)", "金额（不含税）", "金额"),
    "tax_rate": ("税率", "征收率"),
    "tax_amount": ("税额", "税金", "合计税额"),
    "total_amount": ("价税合计", "价税合计金额", "含税金额", "金额(含税)", "金额（含税）"),
    "tax_code": ("税收分类编码", "商品和服务税收分类编码", "税收编码"),
}


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    return re.sub(r"[\s_\-()（）\[\]【】/\\:：]+", "", text)


def _pick(raw: dict[str, Any], aliases: tuple[str, ...]) -> str:
    normalized = [(_norm(key), str(value or "").strip()) for key, value in (raw or {}).items()]
    alias_keys = [_norm(alias) for alias in aliases]
    for alias in alias_keys:
        for key, value in normalized:
            if key == alias and value:
                return value
    for alias in alias_keys:
        if len(alias) < 3:
            continue
        for key, value in normalized:
            if alias in key and value:
                return value
    return ""


def _official_accounting_category(raw: dict[str, Any], goods_name: str) -> str:
    """只从官方字段或官方项目名星号前缀取大类，不按关键词猜。"""
    explicit = _pick(raw, ALIASES["accounting_category"]).strip()
    if explicit:
        return explicit
    match = re.match(r"^\s*[＊*]([^＊*]+)[＊*]", goods_name or "")
    return match.group(1).strip() if match else ""


def _decimal(value: object) -> Decimal | None:
    text = str(value or "").strip().replace(",", "").replace("，", "")
    text = text.replace("¥", "").replace("￥", "").replace("元", "").replace(" ", "")
    if not text or text in {"-", "--", "/", "—"}:
        return None
    if text.endswith("%"):
        text = text[:-1]
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return -parsed if negative else parsed


def _money(value: Decimal | None) -> str | None:
    return None if value is None else f"{value.quantize(Decimal('0.01')):f}"


def _qty(value: Decimal | None) -> str | None:
    return None if value is None else f"{value.normalize():f}"


def _month_range(year: int, month: int) -> tuple[datetime, datetime]:
    if year < 2000 or year > 9999 or month < 1 or month > 12:
        raise ValueError("账期不正确")
    tz = ZoneInfo(settings.TZ)
    start = datetime(year, month, 1, tzinfo=tz)
    end = datetime(year + 1, 1, 1, tzinfo=tz) if month == 12 else datetime(year, month + 1, 1, tzinfo=tz)
    return start, end


def build_finance_summary(db: Session, year: int, month: int) -> dict[str, Any]:
    """生成给财务看的大类汇总；明细保留在系统内，不进入财务主表。"""
    start, end = _month_range(year, month)
    rows = (
        db.query(TaxInvoiceImportRecord, TaxInvoice)
        .join(TaxInvoice, TaxInvoice.id == TaxInvoiceImportRecord.invoice_id)
        .join(TaxInvoiceImport, TaxInvoiceImport.id == TaxInvoiceImportRecord.import_id)
        .filter(
            TaxInvoiceImport.lifecycle == "active",
            TaxInvoiceImportRecord.row_status != "deleted",
            TaxInvoice.source_system == "tax_export",
            TaxInvoice.direction == "output",
            TaxInvoice.status.in_(("issued", "red")),
            TaxInvoice.issue_date >= start,
            TaxInvoice.issue_date < end,
        )
        .order_by(TaxInvoice.issue_date, TaxInvoice.id, TaxInvoiceImportRecord.row_index)
        .all()
    )

    invoice_record_counts: dict[int, int] = defaultdict(int)
    for _, invoice in rows:
        invoice_record_counts[invoice.id] += 1

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []

    for record, invoice in rows:
        raw = record.payload or {}
        goods_name = _pick(raw, ALIASES["goods_name"]).strip()
        category = _official_accounting_category(raw, goods_name)
        tax_rate = _pick(raw, ALIASES["tax_rate"]).strip()
        unit = _pick(raw, ALIASES["unit"]).strip()
        quantity = _decimal(_pick(raw, ALIASES["quantity"]))
        unit_price = _decimal(_pick(raw, ALIASES["unit_price"]))
        amount = _decimal(_pick(raw, ALIASES["amount_excl_tax"]))
        tax_amount = _decimal(_pick(raw, ALIASES["tax_amount"]))
        total_amount = _decimal(_pick(raw, ALIASES["total_amount"]))
        tax_code = _pick(raw, ALIASES["tax_code"]).strip()

        if invoice_record_counts[invoice.id] == 1:
            if amount is None:
                amount = invoice.amount_excl_tax
            if tax_amount is None:
                tax_amount = invoice.tax_amount
            if total_amount is None:
                total_amount = invoice.total_amount
        if total_amount is None and amount is not None and tax_amount is not None:
            total_amount = amount + tax_amount

        reasons: list[str] = []
        if not category:
            reasons.append("缺少官方税收分类名称/项目名分类前缀，禁止自动猜分类")
        if amount is None:
            reasons.append("缺少官方不含税金额")
        if tax_amount is None:
            reasons.append("缺少官方税额")
        if reasons:
            blockers.append({
                "invoiceId": invoice.id,
                "invoiceNumber": invoice.invoice_number,
                "rowIndex": record.row_index,
                "goodsName": goods_name,
                "reasons": reasons,
            })
        if quantity is None:
            warnings.append({
                "invoiceId": invoice.id,
                "invoiceNumber": invoice.invoice_number,
                "rowIndex": record.row_index,
                "warning": "官方明细未提供数量；财务汇总数量留空，不用业务数据补齐",
            })
        if unit_price is None:
            warnings.append({
                "invoiceId": invoice.id,
                "invoiceNumber": invoice.invoice_number,
                "rowIndex": record.row_index,
                "warning": "官方明细未提供单价；系统内部保留缺失，不反推业务单价",
            })

        detail_rows.append({
            "invoiceId": invoice.id,
            "invoiceNumber": invoice.invoice_number,
            "rowIndex": record.row_index,
            "accountingCategory": category,
            "goodsName": goods_name,
            "taxCode": tax_code,
            "taxRate": tax_rate,
            "unit": unit,
            "quantity": _qty(quantity),
            "unitPriceExclTax": _money(unit_price),
            "amountExclTax": _money(amount),
            "taxAmount": _money(tax_amount),
            "totalAmount": _money(total_amount),
            "source": "official_tax_invoice_detail",
            "deletable": False,
        })

        if not category or amount is None or tax_amount is None:
            continue
        key = (category, tax_rate or "未提供")
        group = groups.setdefault(key, {
            "accountingCategory": category,
            "taxRate": tax_rate or "未提供",
            "amountExclTax": Decimal("0"),
            "taxAmount": Decimal("0"),
            "totalAmount": Decimal("0"),
            "invoiceIds": set(),
            "detailCount": 0,
            "units": set(),
            "quantityByUnit": defaultdict(lambda: Decimal("0")),
            "quantityComplete": True,
        })
        group["amountExclTax"] += amount
        group["taxAmount"] += tax_amount
        group["totalAmount"] += total_amount if total_amount is not None else amount + tax_amount
        group["invoiceIds"].add(invoice.id)
        group["detailCount"] += 1
        if quantity is None:
            group["quantityComplete"] = False
        else:
            unit_key = unit or "未提供单位"
            group["units"].add(unit_key)
            group["quantityByUnit"][unit_key] += quantity

    summary_rows: list[dict[str, Any]] = []
    grand_amount = Decimal("0")
    grand_tax = Decimal("0")
    grand_total = Decimal("0")
    for group in sorted(groups.values(), key=lambda x: (x["accountingCategory"], x["taxRate"])):
        units = sorted(group["units"])
        quantity: Decimal | None = None
        unit_label = ""
        if group["quantityComplete"] and len(units) == 1:
            unit_label = units[0]
            quantity = group["quantityByUnit"][unit_label]
        elif len(units) > 1:
            unit_label = "多单位"
        elif not group["quantityComplete"]:
            unit_label = units[0] if len(units) == 1 else ""
        grand_amount += group["amountExclTax"]
        grand_tax += group["taxAmount"]
        grand_total += group["totalAmount"]
        summary_rows.append({
            "accountingCategory": group["accountingCategory"],
            "taxRate": group["taxRate"],
            "quantity": _qty(quantity),
            "unit": unit_label,
            "quantityComplete": bool(group["quantityComplete"] and len(units) <= 1),
            "amountExclTax": _money(group["amountExclTax"]),
            "taxAmount": _money(group["taxAmount"]),
            "totalAmount": _money(group["totalAmount"]),
            "invoiceCount": len(group["invoiceIds"]),
            "detailCount": group["detailCount"],
        })

    return {
        "period": f"{year}-{month:02d}",
        "policy": {
            "deliveryView": "accounting_category_plus_tax_rate",
            "detailStorage": "retained_internal_only",
            "detailDeletable": False,
            "sourcePriority": "official_tax_invoice_only",
            "businessFallback": False,
            "officialCategoryPrefixSupported": True,
            "note": "财务主表按大类汇总；发票号、商品、数量、单价、税额等原始明细永久保留在系统内，不作为财务主表逐条发送。",
        },
        "readyForFinanceDelivery": len(blockers) == 0 and len(summary_rows) > 0,
        "summary": {
            "categoryRowCount": len(summary_rows),
            "detailCount": len(detail_rows),
            "amountExclTax": _money(grand_amount),
            "taxAmount": _money(grand_tax),
            "totalAmount": _money(grand_total),
            "blockerCount": len(blockers),
            "warningCount": len(warnings),
        },
        "categories": summary_rows,
        "blockers": blockers,
        "warnings": warnings,
        "details": detail_rows,
    }


def to_finance_csv(report: dict[str, Any]) -> bytes:
    """只导出财务大类汇总，不把内部逐条明细放进财务文件。"""
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(["账期", report["period"]])
    writer.writerow(["口径", "按财务大类 + 税率汇总；底层开票明细系统内永久保留，不随主表逐条发送"])
    writer.writerow([])
    writer.writerow(["财务大类", "税率", "数量", "单位", "不含税金额", "税额", "价税合计", "发票数", "底层明细数"])
    for row in report.get("categories", []):
        writer.writerow([
            row["accountingCategory"], row["taxRate"], row["quantity"] or "", row["unit"],
            row["amountExclTax"], row["taxAmount"], row["totalAmount"],
            row["invoiceCount"], row["detailCount"],
        ])
    writer.writerow([])
    summary = report.get("summary", {})
    writer.writerow(["合计", "", "", "", summary.get("amountExclTax", ""),
                     summary.get("taxAmount", ""), summary.get("totalAmount", ""), "", ""])
    return buf.getvalue().encode("utf-8-sig")
