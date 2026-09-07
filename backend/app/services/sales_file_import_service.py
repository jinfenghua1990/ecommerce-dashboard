"""吉客云《销售单查询》Excel → 本地销售业绩数据源（sales_orders / sales_order_items）。

背景（用户口径，2026-09-07 拍板）：
- 吉客云开放平台 API 有到期风险；到期后销售业绩改由用户从吉客云客户端导出
  《销售单查询》（含「销售单」+「销售单货品」两个 sheet）手工导入本通道。
- 金额/业绩维度一律以用户上传表格为准（与采购侧「入库申请单货品」口径一致）。
- 本通道只落「有效销售单」（已完成/发货在途/待发货 等），取消/待审核/作废单不导入
  （避免污染业绩聚合）；若库内已有同号订单被本文件标记为取消，则删除该残留。
- 幂等：按 JY 订单号 upsert，重复导入覆盖更新；订单明细整单替换。

时间口径：吉客云导出的「处理时间」是相对时长（如 15小时44分钟），不可用；
付款时间 是唯一可靠绝对时间 → ordered_at / paid_at 一律取付款时间，缺省回退发货时间。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.models.sales import SalesOrder, SalesOrderItem

MAX_FILE_BYTES = 100 * 1024 * 1024

# 销售单主表：吉客云导出列名 → 模型字段
_ORDER_KEYS = ("订单编号",)
_STATUS_KEYS = ("订单状态",)
_CHANNEL_KEYS = ("销售渠道", "店铺", "店铺名称")
_TYPE_KEYS = ("订单类型",)
_SETTLE_KEYS = ("结算状态",)
_ORDERTIME_KEYS = ("下单时间", "订单时间", "创建时间", "拍下时间")
_PAYTIME_KEYS = ("付款时间",)
_SHIPTIME_KEYS = ("发货时间", "出库时间")
_AMOUNT_KEYS = ("应收合计", "订单金额", "应付金额")
_PAID_KEYS = ("实付金额", "实收金额", "成交金额", "金额")
_COST_KEYS = ("订单货品成本", "成本")
_GROSS_KEYS = ("毛利",)
_QTY_KEYS = ("货品数量", "数量")
_WAREHOUSE_KEYS = ("发货仓库",)
_NETNO_KEYS = ("网店订单号", "平台单号", "外部单号", "原始单号")
_LOGISTICS_KEYS = ("物流单号",)
_EXPRESS_KEYS = ("物流公司",)

# 销售单货品：明细列名 → 模型字段
_ITEM_ORDER_KEYS = ("订单编号",)
_ITEM_SKU_KEYS = ("货品编号", "货品编码", "商品编号", "SKU编码")
_ITEM_NAME_KEYS = ("货品名称", "商品名称", "品名")
_ITEM_QTY_KEYS = ("数量", "基本数量")
_ITEM_PRICE_KEYS = ("单价", "成交单价", "销售单价")
_ITEM_AMOUNT_KEYS = ("金额", "行金额", "销售额")
_ITEM_DISCOUNT_KEYS = ("优惠", "优惠金额")
_ITEM_SPEC_KEYS = ("规格", "规格名称")
_ITEM_UNIT_KEYS = ("单位", "基本单位")
_ITEM_GIFT_KEYS = ("赠品",)

# 不参与业绩聚合的状态（前缀/精确匹配）
_CANCELLED_PREFIX = ("已取消", "作废", "已关闭")
_PENDING_STATUS = ("待审核", "未审核")

_ts_fmts = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d")


def _cell(row: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for k in aliases:
        v = row.get(k)
        if v is not None and str(v).strip() != "":
            return v
    return None


def _num(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("¥", "")
    if not text:
        return None
    try:
        return round(float(text), 4)
    except ValueError:
        return None


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or "小时" in text or "分钟" in text:
        return None
    for fmt in _ts_fmts:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    # 纯日期字符串（Excel 原生 date）
    if isinstance(value, datetime):
        return value
    return None


def _row_headers(ws) -> dict[str, int]:
    for row in ws.iter_rows(values_only=True):
        headers = {str(c).strip(): i for i, c in enumerate(row) if c is not None}
        if len(headers) >= 3:
            return headers
    return {}


def _is_valid_status(status: str | None) -> bool:
    if not status:
        return True
    s = str(status).strip()
    if s in _PENDING_STATUS:
        return False
    return not s.startswith(_CANCELLED_PREFIX)


def import_sales_file(
    db: Session,
    *,
    content: bytes,
    original_name: str,
    actor: str,
) -> dict:
    """导入吉客云《销售单查询》导出：销售单 + 销售单货品 双 sheet。返回统计。"""
    if not content:
        raise ValueError("空文件")
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("文件超过 100 MiB 上限")

    wb = load_workbook(BytesIO(content), read_only=True, data_only=True)
    order_ws = None
    item_ws = None
    for name in wb.sheetnames:
        ws = wb[name]
        hd = _row_headers(ws)
        if order_ws is None and _cell(hd, _ORDER_KEYS) is not None and _cell(hd, _CHANNEL_KEYS) is not None and _cell(hd, _STATUS_KEYS) is not None:
            order_ws = (ws, hd)
        elif item_ws is None and _cell(hd, _ITEM_ORDER_KEYS) is not None and _cell(hd, _ITEM_SKU_KEYS) is not None:
            item_ws = (ws, hd)
    if order_ws is None:
        raise ValueError("未识别到「销售单」sheet（需要含 订单编号/销售渠道/订单状态 列）")

    sha = hashlib.sha256(content).hexdigest()[:16]
    imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ---------- sheet1 销售单：upsert 有效订单 ----------
    ws, hd = order_ws
    valid_rows: list[dict] = []
    cancelled_nos: set[str] = set()
    order_idx = 0
    for row in ws.iter_rows(values_only=True):
        if not row:
            continue
        payload = {name: row[i] for name, i in hd.items() if i < len(row)}
        order_no = str(_cell(payload, _ORDER_KEYS) or "").strip()
        # 跳过表头重复行/合计等垃圾行：订单号列等于列名、或非 JY 单号前缀
        if not order_no or order_no == "订单编号" or not order_no.startswith("JY"):
            continue
        status = str(_cell(payload, _STATUS_KEYS) or "").strip()
        if not _is_valid_status(status):
            cancelled_nos.add(order_no)
            continue
        valid_rows.append(payload)
        order_idx += 1

    seen: set[str] = set()
    created = updated = 0
    for payload in valid_rows:
        order_no = str(_cell(payload, _ORDER_KEYS)).strip()
        if order_no in seen:
            continue
        seen.add(order_no)
        order_time = (
            _dt(_cell(payload, _ORDERTIME_KEYS))
            or _dt(_cell(payload, _PAYTIME_KEYS))
            or _dt(_cell(payload, _SHIPTIME_KEYS))
        )
        pay_at = _dt(_cell(payload, _PAYTIME_KEYS)) or order_time
        row = db.query(SalesOrder).filter_by(order_no=order_no).first()
        raw = {
            "netOrderNo": str(_cell(payload, _NETNO_KEYS) or ""),
            "orderStatus": str(_cell(payload, _STATUS_KEYS) or ""),
            "settleStatus": str(_cell(payload, _SETTLE_KEYS) or ""),
            "warehouse": str(_cell(payload, _WAREHOUSE_KEYS) or ""),
            "logisticsNo": str(_cell(payload, _LOGISTICS_KEYS) or ""),
            "logisticsCompany": str(_cell(payload, _EXPRESS_KEYS) or ""),
            "goodsCount": _num(_cell(payload, _QTY_KEYS)),
            "goodsCost": _num(_cell(payload, _COST_KEYS)),
            "grossProfit": _num(_cell(payload, _GROSS_KEYS)),
            "_source": "jky_sales_file",
            "_file": original_name,
            "_sha": sha,
            "_importedAt": imported_at,
        }
        if row is None:
            row = SalesOrder(
                order_no=order_no,
                source_provider="jky_file",
                source_order_id=order_no,
                raw=raw,
            )
            db.add(row)
            created += 1
        else:
            row.source_provider = "jky_file"
            row.source_order_id = order_no
            row.raw = raw
            updated += 1
        row.platform = str(_cell(payload, _CHANNEL_KEYS) or "").strip()
        row.order_type = str(_cell(payload, _TYPE_KEYS) or "").strip()
        row.order_status = str(_cell(payload, _STATUS_KEYS) or "").strip()
        row.pay_status = str(_cell(payload, _SETTLE_KEYS) or "").strip()
        amount = _num(_cell(payload, _AMOUNT_KEYS))
        paid = _num(_cell(payload, _PAID_KEYS))
        row.order_amount = amount if amount is not None else (paid if paid is not None else row.order_amount)
        row.paid_amount = paid if paid is not None else (amount if amount is not None else row.paid_amount)
        row.ordered_at = order_time
        row.paid_at = pay_at

    # 库内残留的、被本文件标记为取消的单 → 删除（以清单为准）
    removed_cancelled = 0
    if cancelled_nos:
        leftovers = (
            db.query(SalesOrder)
            .filter(SalesOrder.order_no.in_(list(cancelled_nos)))
            .all()
        )
        for row in leftovers:
            db.query(SalesOrderItem).filter(SalesOrderItem.order_id == row.id).delete()
            db.delete(row)
            removed_cancelled += 1

    db.flush()

    # ---------- sheet2 销售单货品：整单替换明细 ----------
    item_total = 0
    item_order_hits = 0
    if item_ws is not None:
        iws, ihd = item_ws
        item_groups: dict[str, list[dict]] = {}
        for row in iws.iter_rows(values_only=True):
            if not row:
                continue
            payload = {name: row[i] for name, i in ihd.items() if i < len(row)}
            no = str(_cell(payload, _ITEM_ORDER_KEYS) or "").strip()
            if not no or not _cell(payload, _ITEM_SKU_KEYS):
                continue
            item_groups.setdefault(no, []).append(payload)
        db_orders = {
            o.order_no: o
            for o in db.query(SalesOrder).filter(SalesOrder.order_no.in_(list(item_groups.keys()))).all()
        }
        for no, lines in item_groups.items():
            order = db_orders.get(no)
            if order is None:
                continue
            db.execute(delete(SalesOrderItem).where(SalesOrderItem.order_id == order.id))
            for line in lines:
                qty = _num(_cell(line, _ITEM_QTY_KEYS)) or 0
                unit = _num(_cell(line, _ITEM_PRICE_KEYS))
                amt = _num(_cell(line, _ITEM_AMOUNT_KEYS))
                discount = _num(_cell(line, _ITEM_DISCOUNT_KEYS))
                db.add(SalesOrderItem(
                    order_id=order.id,
                    sku_code=str(_cell(line, _ITEM_SKU_KEYS) or "").strip(),
                    goods_name=str(_cell(line, _ITEM_NAME_KEYS) or "").strip(),
                    quantity=qty,
                    unit_price=unit,
                    amount=amt,
                    discount_amount=discount,
                    raw={
                        "spec": str(_cell(line, _ITEM_SPEC_KEYS) or ""),
                        "unitName": str(_cell(line, _ITEM_UNIT_KEYS) or ""),
                        "gift": str(_cell(line, _ITEM_GIFT_KEYS) or ""),
                        "_source": "jky_sales_file",
                    },
                ))
                item_total += 1
            item_order_hits += 1

    db.commit()

    dates = [
        r[0] for r in db.query(SalesOrder.ordered_at)
        .filter(SalesOrder.source_provider == "jky_file", SalesOrder.ordered_at.isnot(None))
        .all()
    ]
    stats = {
        "ok": True,
        "ordersInFile": order_idx + len(cancelled_nos),
        "ordersImported": len(seen),
        "created": created,
        "updated": updated,
        "cancelledSkipped": len(cancelled_nos),
        "removedCancelled": removed_cancelled,
        "itemsImported": item_total,
        "itemOrderHits": item_order_hits,
        "minDate": min(dates).strftime("%Y-%m-%d") if dates else None,
        "maxDate": max(dates).strftime("%Y-%m-%d") if dates else None,
        "file": original_name,
    }
    audit(db, actor, "sales_file.import", "sales_orders", len(seen),
          {"ordersImported": len(seen), "cancelledSkipped": len(cancelled_nos),
           "removedCancelled": removed_cancelled, "items": item_total})
    return stats
