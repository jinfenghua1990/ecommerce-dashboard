"""采购工作台（截图版）服务：单页呈现 1688 → 采购 → 入库 → 发票 全流程。

与 /procurement-workbench 5 步骤视角互补：
- /procurement-workbench：执行漏斗 + 时间分组列表 + 工作面板（聚焦"接下来该做什么"）
- /procurement-board：单页看板，顶部 6 统计卡 + 左列表 + 右详情（含流程状态横条 + 供应商档案
  + 常购 SKU 表 + 金额与付款分层）

口径与 ChainPrefetch / supplier_summaries / supplier_detail / order_supplier_history 共用，
避免维护多套事实。
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.services.procurement_chain_service import (
    ChainPrefetch,
    _order_row,
    _source_pairs,
    supplier_detail,
)


# ---------- 顶部统计卡 ----------

def overview(db: Session) -> dict:
    """5 数字 + 1 付款率进度环。"""
    pf = ChainPrefetch(db)
    rows = [_order_row(db, order, external, pf=pf) for order, external in _source_pairs(db)]
    today = _today()

    today_count = 0
    refine_count = 0      # 待完善采购
    po_count = 0          # 待采购单
    inbound_count = 0     # 待入库
    invoice_count = 0     # 待发票
    paid_amount = 0.0     # 已付款（来自 settlements 中已支付单）
    total_amount = 0.0    # 应付款总额
    yesterday_count = 0   # 昨日新增订单（用于真实 delta 计算）

    for row in rows:
        amount = row.get("amount") or 0
        total_amount += amount
        # 今日 / 昨日新增订单（按 order_date 兜底 created_at）
        d = row.get("orderDate")
        if d:
            try:
                dt = datetime.fromisoformat(d).date() if isinstance(d, str) else d.date()
                if dt == today:
                    today_count += 1
                elif dt == today - timedelta(days=1):
                    yesterday_count += 1
            except Exception:
                pass
        # 状态判定：基于 stage_done_map 的简化版本（首次未完成环节）
        first_undone = _first_undone_label(row)
        if first_undone == "refine":
            refine_count += 1
        elif first_undone == "po":
            po_count += 1
        elif first_undone == "inbound":
            inbound_count += 1
        elif first_undone == "invoice":
            invoice_count += 1
        # 付款：settlement.paid = True 计入
        for s in row.get("settlement") or []:
            if s.get("paid"):
                paid_amount += s.get("paidAmount") or 0

    paid_rate = round(paid_amount / total_amount * 100) if total_amount else 0
    return {
        "todayNewOrders": today_count,
        "todayDelta": today_count - yesterday_count,
        "pendingRefine": refine_count,
        "pendingPo": po_count,
        "pendingInbound": inbound_count,
        "pendingInvoice": invoice_count,
        "paidRate": paid_rate,
        "paidAmount": round(paid_amount, 2),
        "totalAmount": round(total_amount, 2),
    }


def _today() -> date:
    return datetime.now().date()


def _first_undone_label(row: dict) -> str | None:
    """简化版首次未完成环节：refine / po / inbound / invoice / None（已完成）。"""
    allocations = row.get("allocations") or []
    purchase_orders = row.get("purchaseOrders") or []
    inbound = row.get("inbound") or []
    invoice = row.get("invoice") or []

    # ② 采购内容 = 细化 + allocations
    if not row.get("purchaseContentComplete") or not allocations:
        return "refine"
    # ③/④ 吉客云采购单
    if not purchase_orders:
        return "po"
    # ⑤ 入库
    if not inbound:
        return "inbound"
    # ⑥ 发票
    if not invoice:
        return "invoice"
    return None


# ---------- 左侧订单列表 ----------

def list_board_orders(
    db: Session,
    status: str = "all",      # all / pending / refine / po / inbound / invoice / done
    q: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict:
    pf = ChainPrefetch(db)
    rows = [_order_row(db, order, external, pf=pf) for order, external in _source_pairs(db)]
    today = _today()
    out: list[dict] = []
    for row in rows:
        first = _first_undone_label(row)
        if status == "pending" and first is None:
            continue
        if status == "done" and first is not None:
            continue
        if status in ("refine", "po", "inbound", "invoice") and first != status:
            continue
        if q:
            ql = q.lower()
            hay = f"{row.get('orderNo','')} {row.get('supplier','')}".lower()
            if ql not in hay:
                continue
        steps = _step_progress(row)
        out.append({
            "orderId": row["orderId"],
            "orderNo": row["orderNo"],
            "supplier": row.get("supplier") or "",
            "amount": row.get("amount"),
            "orderDate": row.get("orderDate"),
            "orderStatus": row.get("orderStatus") or "",
            "firstUndone": first,
            "firstUndoneLabel": _status_label(first),
            "stepProgress": steps,
        })

    out.sort(key=lambda r: r.get("orderDate") or "", reverse=True)
    total = len(out)
    start = max(0, (page - 1) * page_size)
    paged = out[start:start + page_size]

    # 按时间分组
    group_order: list[str] = []
    groups: dict[str, list[dict]] = {}
    for item in paged:
        label = _time_group(_parse_dt(item.get("orderDate")), today)
        if label not in groups:
            group_order.append(label)
            groups[label] = []
        groups[label].append(item)
    return {
        "total": total,
        "page": page,
        "pageSize": page_size,
        "groups": [{"label": k, "items": groups[k]} for k in group_order],
    }


def _status_label(first: str | None) -> str:
    return {
        "refine": "待完善",
        "po": "待采购单",
        "inbound": "待入库",
        "invoice": "待发票",
        None: "已完成",
    }.get(first, "待完善")


def _step_progress(row: dict) -> list[dict]:
    """5 步骤完成情况：[done(bool), label(str)]。"""
    allocations = row.get("allocations") or []
    purchase_orders = row.get("purchaseOrders") or []
    inbound = row.get("inbound") or []
    invoice = row.get("invoice") or []
    return [
        {"label": "1688", "done": True},
        {"label": "采购", "done": bool(allocations) and bool(row.get("purchaseContentComplete"))},
        {"label": "采购单", "done": bool(purchase_orders)},
        {"label": "入库", "done": bool(inbound)},
        {"label": "发票", "done": bool(invoice)},
    ]


def _parse_dt(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _time_group(t: datetime | None, today: date) -> str:
    if t is None:
        return "未注时间"
    d = t.date() if isinstance(t, datetime) else t
    if d == today:
        return "今天"
    if d == today - timedelta(days=1):
        return "昨天"
    delta_days = (today - d).days
    if 0 < delta_days < 7:
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        return weekdays[d.weekday()]
    if d.year == today.year:
        return f"{d.month}月{d.day}日"
    return d.isoformat()


# ---------- 右侧详情（5 卡） ----------

def order_detail(db: Session, order_id: int) -> dict | None:
    """单个订单详情：订单基本信息 + 流程状态 + 供应商档案 + 常购 SKU + 金额与付款分层。"""
    pf = ChainPrefetch(db)
    matched = [(o, e) for o, e in _source_pairs(db) if o is not None and o.id == order_id]
    if not matched:
        return None
    order, external = matched[0]
    row = _order_row(db, order, external, pf=pf)
    supplier_name = (row.get("supplier") or "").strip()

    # 流程状态：5 步 done + label
    flow = _flow_status(row)
    # 供应商档案
    profile = _supplier_profile(db, row, supplier_name) if supplier_name else None
    # 常购 SKU：该供应商所有 allocations 聚合
    often_skus = _supplier_often_skus(db, supplier_name) if supplier_name else []
    # 金额与付款分层
    breakdown = _payment_breakdown(row)

    return {
        "order": {
            "orderId": row["orderId"],
            "orderNo": row["orderNo"],
            "supplier": row.get("supplier"),
            "buyer": row.get("buyer"),
            "amount": row.get("amount"),
            "orderDate": row.get("orderDate"),
            "orderStatus": row.get("orderStatus"),
            "title": row.get("title"),
            "freight": row.get("freight"),
            "discount": row.get("discount"),
            "goodsTotal": row.get("goodsTotal"),
        },
        "flowStatus": flow,
        "supplierProfile": profile,
        "oftenSkus": often_skus,
        "allocations": row.get("allocations") or [],
        "purchaseOrders": row.get("purchaseOrders") or [],
        "inbound": row.get("inbound") or [],
        "invoice": row.get("invoice") or [],
        "verified": bool(row.get("verified")),
        "paymentBreakdown": breakdown,
    }


def _flow_status(row: dict) -> list[dict]:
    """5 步流程状态横条数据。"""
    allocations = row.get("allocations") or []
    purchase_orders = row.get("purchaseOrders") or []
    inbound = row.get("inbound") or []
    invoice = row.get("invoice") or []
    settlement = row.get("settlement") or []
    paid_any = any(s.get("paid") for s in settlement)

    def step(no: int, label: str, done: bool, detail: str) -> dict:
        return {"no": no, "label": label, "done": done, "detail": detail}

    return [
        step(1, "1688 订单", True, "已下单"),
        step(
            2,
            "采购内容",
            bool(allocations) and bool(row.get("purchaseContentComplete")),
            "已细化" if allocations else "待完善",
        ),
        step(3, "吉客云采购单", bool(purchase_orders),
             f"已生成 {len(purchase_orders)} 张" if purchase_orders else "待生成"),
        step(4, "入库", bool(inbound),
             f"已入库 {len(inbound)} 单" if inbound else "待入库"),
        step(5, "发票", bool(invoice),
             f"已收票 {len(invoice)} 张" if invoice else ("待收票" + ("（已付款）" if paid_any else ""))),
    ]


def _supplier_profile(db: Session, row: dict, supplier_name: str) -> dict:
    """供应商档案：30 天采购次数 / 累计金额 / 最近日期 / 标签。"""
    detail = supplier_detail(db, supplier_name) or {}
    today = _today()
    last_date = detail.get("lastOrderDate")
    last_recent = "近 30 天"
    if last_date:
        try:
            dt = datetime.fromisoformat(last_date).date() if isinstance(last_date, str) else last_date.date()
            if (today - dt).days <= 30:
                last_recent = "近 30 天"
            elif (today - dt).days <= 90:
                last_recent = "近 90 天"
            else:
                last_recent = f"{(today - dt).days} 天前"
        except Exception:
            pass
    return {
        "supplierName": supplier_name,
        "badge": "优选",   # 简化标签
        "orderCount30d": detail.get("orderCount", 0),
        "totalPurchase": detail.get("totalPurchase", 0),
        "lastOrderDate": last_date,
        "lastRecentLabel": last_recent,
    }


def _supplier_often_skus(db: Session, supplier_name: str) -> list[dict]:
    """该供应商所有订单的 allocations 聚合 SKU 频次。"""
    pf = ChainPrefetch(db)
    rows = [_order_row(db, order, external, pf=pf) for order, external in _source_pairs(db)]
    matched = [r for r in rows if (r.get("supplier") or "").strip() == supplier_name]
    counter: Counter = Counter()
    sku_meta: dict[str, dict] = {}
    for r in matched:
        for a in r.get("allocations") or []:
            code = a.get("skuCode") or ""
            if not code:
                continue
            counter[code] += 1
            if code not in sku_meta:
                sku_meta[code] = {"goodsName": a.get("goodsName") or "", "unitPrice": a.get("unitPrice") or 0}
    result = []
    for code, count in counter.most_common(8):
        meta = sku_meta[code]
        result.append({
            "skuCode": code,
            "goodsName": meta["goodsName"],
            "purchaseCount": count,   # 采购次数
            "qty": count,             # 数量（次数即数量）
            "unitPrice": meta["unitPrice"],
            "amount": round(count * (meta["unitPrice"] or 0), 2),
        })
    return result


def _payment_breakdown(row: dict) -> dict:
    """金额与付款分层 8 列。"""
    # 商品金额优先用 1688 订单的 goods_total，否则回退为实付款额
    goods_total = float(row.get("goodsTotal") or row.get("amount") or 0)
    freight = float(row.get("freight") or 0)
    extra = sum(float(item.get("amount") or 0) for item in row.get("expenses") or [])
    discount = float(row.get("discount") or 0)
    total_due = goods_total + freight + extra - discount
    settlement = row.get("settlement") or []
    paid_amount = sum((s.get("paidAmount") or s.get("amount") or 0) for s in settlement if s.get("paid"))
    unpaid_amount = max(0, round(total_due - paid_amount, 2))
    diff_amount = 0.0
    return {
        "goodsAmount": round(goods_total, 2),
        "freight": round(freight, 2),
        "extra": round(extra, 2),
        "discount": round(discount, 2),
        "totalDue": round(total_due, 2),
        "paidAmount": round(paid_amount, 2),
        "unpaidAmount": unpaid_amount,
        "diffAmount": round(diff_amount, 2),
    }
