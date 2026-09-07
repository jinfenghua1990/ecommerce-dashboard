"""采购执行中心（5 步骤任务视角）服务。

与 7 环节链路（追踪视角）共用 ChainPrefetch 与 _order_row，避免维护两套事实。
- 5 步 ① 包含 7 环节 ①
- 5 步 ② = 7 环节 ② 的一部分（purchaseContentComplete）
- 5 步 ③ 是 7 环节 ② 的细分（SKU 关联到吉客云）
- 5 步 ④ = 7 环节 ③
- 5 步 ⑤ = 7 环节 ④⑤⑥⑦ 的并集
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.audit import audit
from app.models.alibaba1688_import import Alibaba1688Order
from app.models.consumable import Consumable
from app.models.ops import ExceptionRecord
from app.models.purchase import ExternalPurchaseOrder
from app.services.procurement_chain_service import (
    ChainPrefetch,
    _order_row,
    _source_pairs,
    order_supplier_history,
    supplier_detail,
    supplier_summaries,
)
from app.services.purchase_service import update_external_po
from app.utils.money import to_decimal

# 订单类型：正品 / 耗材（外包装等包材采购）。
# 耗材采购的 1688 订单与正常货品订单同列表管理，仅以标签区分，便于采购员识别跟进。
# 判定口径：① 1688 订单号出现在耗材历史档案的「采购订货号」；② 供应商名含包装/印刷等关键词。
_CONSUMABLE_SELLER_KEYWORDS = ("包装", "印刷", "印务", "耗材", "包材")


def _consumable_source_nos(db: Session) -> set[str]:
    """耗材档案（consumables.raw.row.采购订货号）引用过的采购订单号集合。"""
    nos: set[str] = set()
    for (raw,) in db.query(Consumable.raw).all():
        if not raw or not isinstance(raw, dict):
            continue
        no = (raw.get("row") or {}).get("采购订货号")
        if no:
            nos.add(str(no).strip())
    return nos


def _order_kind(order_no: str | None, supplier: str | None, consumable_nos: set[str],
                override: str = "", has_consumable_hc: bool = False) -> str:
    """返回订单类型：consumable=耗材（包材）采购 / goods=正常货品。

    人工覆盖优先（耗材档案 Excel 的「采购订货号」可能填错，自动判定仅作默认）。
    判定优先级：人工覆盖 > 挂有非取消耗材 HC 单 > 耗材档案采购订货号 > 供应商关键词 > 正品。
    挂了 HC 单是平台上最直接的「货品类型」信号（2026-09-07：手工录入单默认正品且不更新，
    用户要求按货品类型自动带出）。
    """
    if override in ("goods", "consumable"):
        return override
    if has_consumable_hc:
        return "consumable"
    if order_no and order_no.strip() in consumable_nos:
        return "consumable"
    name = supplier or ""
    return "consumable" if any(k in name for k in _CONSUMABLE_SELLER_KEYWORDS) else "goods"


def edit_order_main_fields(
    db: Session,
    *,
    order_id: int,
    actor: str = "",
    supplier_name: str | None = None,
    title: str | None = None,
    ordered_at=None,
    goods_total=None,
    freight=None,
    discount=None,
    actual_payment=None,
    order_amount=None,
    paid_amount=None,
    platform: str | None = None,
) -> dict:
    """网页交互编辑订单主档（供应商/标题/金额等），全部留审计。

    - 1688 导入订单（order_id>0）：改的是源单事实字段（供应商 / 商品金额 / 运费 /
      优惠 / 实付），并按导入口径同步工作流副本：
      ``order_amount = goods + freight - discount``、``paid_amount = 实付``。
    - 工作流独有订单（order_id<0，无 1688 原件）：直接更新副本主档字段
      （复用采购中心 update_external_po，自带校验与审计）。
    """
    order: Alibaba1688Order | None = None
    external: ExternalPurchaseOrder | None = None
    for candidate_order, candidate_external in _source_pairs(db):
        candidate_id = candidate_order.id if candidate_order is not None else -candidate_external.id
        if candidate_id == order_id:
            order, external = candidate_order, candidate_external
            break
    if order is None and external is None:
        raise ValueError("订单不存在或已被删除")

    if order is None:
        update_external_po(
            db, external,  # type: ignore[arg-type]
            platform=platform, supplier_name=supplier_name, title=title, ordered_at=ordered_at,
            order_amount=order_amount, paid_amount=paid_amount, actor=actor,
        )
        return {"ok": True, "mode": "external", "orderNo": external.external_order_id}  # type: ignore[union-attr]

    def _money(value, label: str, *, non_negative: bool = True) -> Decimal:
        parsed = to_decimal(value)
        if parsed is None or not parsed.is_finite():
            raise ValueError(f"{label}必须是有效数字")
        if non_negative and parsed < 0:
            raise ValueError(f"{label}不能为负数")
        return parsed.quantize(Decimal("0.01"))

    before: dict[str, object] = {}
    after: dict[str, object] = {}
    if supplier_name is not None:
        name = supplier_name.strip()
        if not name:
            raise ValueError("供应商不能为空")
        before["supplier"] = order.seller_company_name
        order.seller_company_name = name
        after["supplier"] = name
    money_changed = False
    if goods_total is not None:
        value = _money(goods_total, "商品金额")
        before["goodsTotal"] = str(order.goods_total)
        order.goods_total = value
        after["goodsTotal"] = str(value)
        money_changed = True
    if freight is not None:
        value = _money(freight, "运费")
        before["freight"] = str(order.freight)
        order.freight = value
        after["freight"] = str(value)
        money_changed = True
    if discount is not None:
        value = _money(discount, "优惠/调整", non_negative=False)
        before["discount"] = str(order.discount)
        order.discount = value
        after["discount"] = str(value)
        money_changed = True
    if actual_payment is not None:
        value = _money(actual_payment, "实付金额")
        before["paidAmount"] = str(order.actual_payment)
        order.actual_payment = value
        after["paidAmount"] = str(value)
        money_changed = True
    if title is not None and external is not None:
        before["title"] = external.title
        external.title = title.strip()
        after["title"] = external.title
    if external is not None:
        if money_changed:
            before.setdefault("copyOrderAmount", str(external.order_amount))
            before.setdefault("copyPaidAmount", str(external.paid_amount))
            external.order_amount = (order.goods_total or Decimal("0")) + (order.freight or Decimal("0")) - (order.discount or Decimal("0"))
            external.paid_amount = order.actual_payment
            after["copyOrderAmount"] = str(external.order_amount)
            after["copyPaidAmount"] = str(external.paid_amount)
        if "supplier" in after:
            external.supplier_name = order.seller_company_name
    audit(db, actor, "procurement.workbench.order_edit_main", "alibaba1688_orders", order.id,
          {"orderNo": order.external_order_id, "before": before, "after": after})
    return {"ok": True, "mode": "file", "orderNo": order.external_order_id}


def rename_supplier(db: Session, *, old_name: str, new_name: str, actor: str = "") -> dict:
    """供应商改名/归一：把该供应商全部订单的供应商写法统一改为新名称。

    - 1688 源单副本 ``seller_company_name`` 与工作流副本 ``supplier_name``
      按旧名精确匹配后统一改写（与单笔订单改名 edit_order_main_fields 同口径，
      供应商聚合视图优先取源单卖家名，两边都要改才生效）；
    - 新名称若与现有供应商相同即合并（聚合视图按名称实时归组）；
    - 发票 ``seller_name`` 是税务事实不改名；订单名归一后发票按名称匹配的
      命中率自然提升；
    - 全程写审计。
    """
    old_name = (old_name or "").strip()
    new_name = (new_name or "").strip()
    if not old_name:
        raise ValueError("原供应商名称不能为空")
    if not new_name:
        raise ValueError("新供应商名称不能为空")
    if old_name == new_name:
        raise ValueError("新名称与当前名称相同")

    po_rows = (
        db.query(ExternalPurchaseOrder)
        .filter(ExternalPurchaseOrder.supplier_name == old_name)
        .all()
    )
    file_rows = (
        db.query(Alibaba1688Order)
        .filter(Alibaba1688Order.seller_company_name == old_name)
        .all()
    )
    if not po_rows and not file_rows:
        raise ValueError(f"未找到名称为「{old_name}」的供应商订单")

    merged = (
        db.query(ExternalPurchaseOrder.id)
        .filter(ExternalPurchaseOrder.supplier_name == new_name)
        .count()
    ) > 0

    for po in po_rows:
        po.supplier_name = new_name
    for fo in file_rows:
        fo.seller_company_name = new_name

    audit(db, actor, "procurement.workbench.supplier_rename", "supplier", old_name,
          {"oldName": old_name, "newName": new_name, "merged": merged,
           "renamedOrders": len(po_rows), "renamedFileOrders": len(file_rows),
           "poIds": [po.id for po in po_rows], "fileOrderIds": [fo.id for fo in file_rows]})
    db.commit()
    return {
        "ok": True, "oldName": old_name, "newName": new_name,
        "renamedOrders": len(po_rows), "renamedFileOrders": len(file_rows), "merged": merged,
    }

# 5 步骤：采购员日常推进的执行漏斗
# 顺序即采购员在系统里的推进路径；dimension 决定 UI 上的色点（与 7 环节共用配色）。
WORKBENCH_STEPS: list[dict] = [
    {"key": "order",      "no": 1, "label": "1688 已下单",    "short": "1688",    "dimension": "1688",     "href": "/alibaba1688-import", "act": "导入 1688 订单"},
    {"key": "content",    "no": 2, "label": "确认采购内容",    "short": "采购内容", "dimension": "purchase", "href": "/purchase",          "act": "去采购中心细化"},
    {"key": "sku",        "no": 3, "label": "选择吉客云货品",  "short": "选货品",   "dimension": "purchase", "href": "/purchase",          "act": "关联吉客云 SKU"},
    {"key": "jackyun_po", "no": 4, "label": "生成采购单",      "short": "采购单",   "dimension": "jackyun",  "href": "/jackyun-import",    "act": "导入吉客云采购单"},
    {"key": "closeout",   "no": 5, "label": "入库/发票/税务",  "short": "入库发票", "dimension": "jackyun",  "href": "/jackyun-import",    "act": "查看入库/发票"},
]
WORKBENCH_TOTAL = len(WORKBENCH_STEPS)


def _step_states(row: dict) -> dict[str, dict]:
    """5 步骤每步的完成状态 + 简短摘要（与 7 环节口径共享 row 字段）。"""
    allocations = row.get("allocations") or []
    sku_linked = [a for a in allocations if a.get("skuId")]
    purchase_orders = row.get("purchaseOrders") or []
    inbound = row.get("inbound") or []
    # 1688 原始报文的货品明细（含编号）。未关联入库单时没有分配行，
    # 「确认采购内容」环节用它兜底，避免用户看到一片空白（2026-09-07）。
    order_items = row.get("orderItems") or []
    order_item_codes = [i.get("productNumber") for i in order_items if i.get("productNumber")]
    # 耗材收货（本平台自有流程，不生成吉客云单据）：收货即入库，明细自带编号。
    # 已收货的耗材单不能再按「吉客云采购单」那套卡在待确认/待匹配/待生成（2026-09-07）。
    consumable = row.get("consumable") or {}
    consumable_received = bool(consumable.get("received"))
    consumable_items = consumable.get("items") or []
    consumable_codes = [i.get("code") for i in consumable_items if i.get("code")]

    if row.get("purchaseContentComplete"):
        content_detail = "已细化"
    elif consumable_received:
        content_detail = f"耗材已收货 {len(consumable_items)} 项"
        if consumable_codes:
            content_detail += "：" + "、".join(consumable_codes)
    elif allocations:
        content_detail = "未细化实际采购内容"
    elif order_items:
        content_detail = f"待细化 {len(order_items)} 项（1688 明细）"
        if order_item_codes:
            content_detail += "：" + "、".join(order_item_codes)
    else:
        content_detail = "未细化实际采购内容"
    content_amount = (
        sum(a.get("amount") or 0 for a in allocations)
        or sum(i.get("amount") or 0 for i in order_items)
        or None
    )
    inbound_ready = (
        bool(inbound) and all(item.get("consumableUsageDecided") for item in inbound)
    ) or consumable_received
    invoice = row.get("invoice") or []
    settlement = row.get("settlement") or []
    # 吉客云结算单在本账号恒为空，1688 担保交易的实付记录同样是付款事实。
    paid_any = any(s.get("paid") for s in settlement) or bool(row.get("paidOn1688"))
    # 开票口径：采购员只负责催票，认证（勾选抵扣）归财务，不再作为采购台卡点（2026-09-07）。
    invoice_status = row.get("invoiceStatus") or "none"
    invoice_done = invoice_status == "done"
    invoice_outstanding = row.get("invoiceOutstanding")
    if invoice_status == "pending":
        invoice_text = "待供应商开票"
    elif invoice_status == "partial":
        invoice_text = f"待开票 ¥{invoice_outstanding:,.2f}" if invoice_outstanding else "部分开票"
    elif invoice_done:
        invoice_text = f"已开票 ¥{row.get('invoicedAmount') or 0:,.2f}"
    else:
        invoice_text = "待收票"

    return {
        "order": {
            "done": True,
            "detail": row.get("orderNo") or "—",
            "amount": row.get("amount"),
        },
        "content": {
            "done": bool(row.get("purchaseContentComplete")) or consumable_received,
            "detail": content_detail,
            "amount": content_amount,
        },
        "sku": {
            "done": consumable_received or (bool(allocations) and all(a.get("skuId") for a in allocations)),
            "detail": (
                "耗材（本平台）无需吉客云货品" if consumable_received
                else (
                    f"{len(sku_linked)}/{len(allocations)} SKU 已关联吉客云"
                    if allocations else "无 SKU 明细"
                )
            ),
            "amount": None,
        },
        "jackyun_po": {
            "done": consumable_received or bool(purchase_orders) or bool(row.get("jackyunPoBypassed")),
            "detail": (
                "耗材不生成吉客云采购单" if consumable_received
                else (
                    f"已生成 {len(purchase_orders)} 张" if purchase_orders
                    else ("已按 Excel 口径跳过（入库闭环）" if row.get("jackyunPoBypassed") else "未生成采购单")
                )
            ),
            "amount": sum(p.get("amount") or 0 for p in purchase_orders) or None,
        },
        "closeout": {
            # 认证归财务：只要 入库 + 票已开够 + 已付款，采购员这单就算收尾完成。
            "done": inbound_ready and invoice_done and paid_any,
            "detail": (
                ("耗材已入库" if consumable_received else f"入库 {len(inbound)}")
                + " / " + invoice_text
                + (" / 已付款" if paid_any else " / 未付款")
            ) if (inbound or consumable_received or invoice or settlement or paid_any) else "尚未触发",
            "amount": sum(i.get("amount") or 0 for i in invoice) or None,
        },
    }


def _first_undone_step(states: dict[str, dict]) -> str | None:
    """返回最早未完成步骤的 key；全部完成则 None。"""
    for step in WORKBENCH_STEPS:
        if not states[step["key"]]["done"]:
            return step["key"]
    return None


def closeout_stage(row: dict, step_states: dict[str, dict] | None = None) -> str:
    """收尾环节细分到具体卡点，供列表状态标签直接显示（待收票 / 待付款 / 待认证）。

    5 步视图把入库/发票/付款/认证合并成 closeout，笼统显示「待收尾」看不出卡在哪；
    这里按「没入库 -> 票没开够 -> 没付款」的顺序给出真正的缺口。
    认证（勾选抵扣）归财务，不再作为采购员卡点（2026-09-07 用户口径）。
    """
    states = step_states or _step_states(row)
    if states.get("closeout", {}).get("done"):
        return "done"
    # 耗材收货（本平台）等同于入库完成，不能再报「待入库」（2026-09-07）。
    consumable_received = bool((row.get("consumable") or {}).get("received"))
    if not consumable_received and not (row.get("inbound") or []):
        return "awaiting_inbound"
    # 票没开够（含一张没开 / 部分开票）→ 待供应商开票，这是采购员要催的事。
    if (row.get("invoiceOutstanding") or 0) > 0 or not (row.get("invoice") or []):
        return "awaiting_invoice"
    paid_any = any(s.get("paid") for s in (row.get("settlement") or [])) or bool(row.get("paidOn1688"))
    if not paid_any:
        return "awaiting_payment"
    return "open"


# ---------- 接口实现 ----------

def funnel(db: Session) -> dict:
    """5 步漏斗：每步 count = 已完成该步的订单数。"""
    pf = ChainPrefetch(db)
    rows = [_order_row(db, o, ext, pf=pf) for o, ext in _source_pairs(db)]
    total = len(rows)
    done_count = {step["key"]: 0 for step in WORKBENCH_STEPS}
    for row in rows:
        for key, st in _step_states(row).items():
            if st["done"]:
                done_count[key] += 1
    steps = [
        {**step, "count": done_count[step["key"]], "pct": round(done_count[step["key"]] / total * 100) if total else 0}
        for step in WORKBENCH_STEPS
    ]
    return {"total": total, "stepTotal": WORKBENCH_TOTAL, "steps": steps}


def todos(db: Session) -> dict:
    """5 个待办计数：按「最早未完成步骤」归类，加和 = 待处理订单数。"""
    pf = ChainPrefetch(db)
    rows = [_order_row(db, o, ext, pf=pf) for o, ext in _source_pairs(db)]
    total = len(rows)
    counts = {step["key"]: 0 for step in WORKBENCH_STEPS}
    pending_total = 0
    for row in rows:
        states = _step_states(row)
        first = _first_undone_step(states)
        if first is not None:
            counts[first] += 1
            pending_total += 1
    return {
        "total": total,
        "pending": pending_total,
        "items": [
            {**step, "count": counts[step["key"]]}
            for step in WORKBENCH_STEPS
        ],
    }


def summary(db: Session) -> dict:
    """采购工作台六项待办指标，全部基于本地已落库事实。"""
    pf = ChainPrefetch(db)
    rows = [_order_row(db, o, ext, pf=pf) for o, ext in _source_pairs(db)]
    today = datetime.now().date()
    new_orders = 0
    pending_sku = 0
    pending_po = 0
    pending_inbound = 0
    pending_invoice = 0
    for row in rows:
        order_date = _parse_dt(row.get("orderDate"))
        if order_date is not None and order_date.date() == today:
            new_orders += 1
        allocations = row.get("allocations") or []
        if not allocations or any(not allocation.get("skuId") for allocation in allocations):
            pending_sku += 1
        purchase_orders = row.get("purchaseOrders") or []
        sku_complete = bool(allocations) and all(allocation.get("skuId") for allocation in allocations)
        if row.get("purchaseContentComplete") and sku_complete and not purchase_orders \
                and not row.get("jackyunPoBypassed"):
            pending_po += 1
        inbound = row.get("inbound") or []
        if purchase_orders and not inbound_ready:
            pending_inbound += 1
        invoice = row.get("invoice") or []
        if inbound and not invoice:
            pending_invoice += 1
    # 与 list_orders 的 status=exception 筛选口径保持一致：
    # 只统计能明确关联到某张采购单的异常，避免摘要 "异常 2" 但点进去列表为空。
    exception_ids = _exception_order_ids(db)
    exception_count = sum(
        1 for row in rows
        if row["orderId"] in exception_ids or row.get("externalPoId") in exception_ids
    )
    return {
        "totalOrders": len(rows),
        "newOrders": new_orders,
        "pendingSku": pending_sku,
        "pendingPo": pending_po,
        "pendingInbound": pending_inbound,
        "pendingInvoice": pending_invoice,
        "exceptionCount": exception_count,
    }


def _parse_dt(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _time_group_label(t: datetime | None, today: date) -> str:
    """按业务时区把下单时间分组成：今天 / 昨天 / 星期 / M月D日 / 未注时间。"""
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


# 扩展状态筛选：保留旧值，同时提供工作台规格中的业务状态。
_VALID_STATUSES = {
    "all", "pending", "done", "order", "content", "sku", "jackyun_po", "closeout",
    "refine", "po", "inbound", "invoice", "exception",
}


def _exception_order_ids(db: Session) -> set[int]:
    """读取明确关联到采购单的异常；没有 ref 的全局异常不误标所有订单。"""
    rows = (
        db.query(ExceptionRecord)
        .filter(ExceptionRecord.status.in_(("pending", "confirmed")))
        .all()
    )
    ids: set[int] = set()
    for row in rows:
        if row.ref_table in ("alibaba1688_orders", "external_purchase_orders") and row.ref_id:
            try:
                ids.add(int(row.ref_id))
            except (TypeError, ValueError):
                continue
        detail = row.detail or {}
        for key in ("orderId", "order_id", "poId", "po_id"):
            value = detail.get(key)
            if value is not None:
                try:
                    ids.add(int(value))
                except (TypeError, ValueError):
                    pass
    return ids


def _matches_status(row: dict, states: dict[str, dict], first: str | None, status: str,
                    exception_ids: set[int]) -> bool:
    if status == "all":
        return True
    if status == "done":
        return first is None
    if status in ("refine", "content"):
        return not row.get("purchaseContentComplete")
    allocations = row.get("allocations") or []
    purchase_orders = row.get("purchaseOrders") or []
    inbound = row.get("inbound") or []
    invoice = row.get("invoice") or []
    sku_complete = bool(allocations) and all(allocation.get("skuId") for allocation in allocations)
    # 就地计算入库是否完成（耗材收货视同入库）；此前引用了 _step_states 的局部变量，
    # status="inbound" 且订单有采购单时会直接 NameError → 500（2026-09-07 顺手修掉）。
    inbound_ready = (
        bool(inbound) and all(item.get("consumableUsageDecided") for item in inbound)
    ) or bool((row.get("consumable") or {}).get("received"))
    if status == "sku":
        return not allocations or not sku_complete
    if status in ("po", "jackyun_po"):
        return bool(row.get("purchaseContentComplete")) and sku_complete and not purchase_orders \
            and not row.get("jackyunPoBypassed")
    if status == "inbound":
        return bool(purchase_orders) and not inbound_ready
    if status == "invoice":
        # 供应商待开发票（采购员催票口径）：票没开够就算，含完全未开票与部分开票。
        return (row.get("invoiceOutstanding") or 0) > 0 or not invoice
    if status == "exception":
        return row.get("orderId") in exception_ids or row.get("externalPoId") in exception_ids
    if status == "pending":
        return first is not None
    # 精确匹配最早未完成步骤
    return first == status


def list_orders(
    db: Session,
    status: str = "all",
    q: str = "",
    sort_by: str = "date",
    page: int = 1,
    page_size: int = 20,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    """按下单时间倒序、按时间标签分组的订单列表；支持状态和日期筛选。"""
    status = status if status in _VALID_STATUSES else "all"
    pf = ChainPrefetch(db)
    rows = [_order_row(db, o, ext, pf=pf) for o, ext in _source_pairs(db)]
    today = datetime.now().date()
    exception_ids = _exception_order_ids(db)
    consumable_nos = _consumable_source_nos(db)
    out: list[dict] = []
    for row in rows:
        states = _step_states(row)
        first = _first_undone_step(states)
        first_step = next((s for s in WORKBENCH_STEPS if s["key"] == first), None)
        order_date = _parse_dt(row.get("orderDate"))
        order_day = order_date.date() if order_date is not None else None
        if start_date is not None and (order_day is None or order_day < start_date):
            continue
        if end_date is not None and (order_day is None or order_day > end_date):
            continue
        if not _matches_status(row, states, first, status, exception_ids):
            continue
        if q:
            ql = q.lower()
            sku_text = " ".join(f"{a.get('skuCode', '')} {a.get('goodsName', '')}" for a in row.get("allocations", []))
            hay = f"{row.get('orderNo','')} {row.get('supplier','')} {row.get('title','')} {sku_text}".lower()
            if ql not in hay:
                continue
        out.append({
            "orderId": row["fileOrderId"] if row["fileOrderId"] is not None else -row["externalPoId"],
            "externalPoId": row.get("externalPoId"),
            "orderNo": row["orderNo"],
            "platform": row.get("platform") or "other",
            "orderKind": _order_kind(row.get("orderNo"), row.get("supplier"), consumable_nos, row.get("orderKindOverride", ""), bool(row.get("consumable"))),
            "supplier": row.get("supplier") or "",
            "amount": row.get("amount"),
            "freight": row.get("freight"),
            "orderDate": row.get("orderDate"),
            "orderStatus": row.get("orderStatus") or "",
            "purchaseStatus": row.get("purchaseStatus") or "",
            "hasException": row.get("orderId") in exception_ids or row.get("externalPoId") in exception_ids,
            "doneCount": row.get("doneCount") or 0,
            "stageTotal": row.get("stageTotal") or WORKBENCH_TOTAL,
            "firstUndone": first,
            "firstUndoneLabel": first_step["label"] if first_step else "已完成",
            "firstUndoneShort": first_step["short"] if first_step else "已完成",
            "firstUndoneDimension": first_step["dimension"] if first_step else "purchase",
            "stepStates": states,
            "inboundDone": (
                bool(row.get("inbound")) and all(item.get("consumableUsageDecided") for item in row.get("inbound") or [])
            ) or bool((row.get("consumable") or {}).get("received")),
            "invoiceDone": bool(row.get("invoice")),
            # 供应商待开发票（采购员催票口径）：invoicedAmount=已收票、invoiceOutstanding=还差多少。
            # invoiceStatus: pending=完全未开票 / partial=部分开票 / done=已开够 / none=无实付金额
            "invoiceStatus": row.get("invoiceStatus") or "none",
            "invoicedAmount": row.get("invoicedAmount"),
            "invoiceOutstanding": row.get("invoiceOutstanding"),
            # 收尾环节的具体卡点：待入库 / 待开发票 / 待付款，供状态标签直接显示。
            "closeoutStage": closeout_stage(row, states),
        })

    # 排序
    if sort_by == "amount":
        out.sort(key=lambda r: (r.get("amount") or 0), reverse=True)
    elif sort_by == "invoice":
        # 催票视角：未开票金额大的排前面，已开够的沉底。
        out.sort(key=lambda r: (r.get("invoiceOutstanding") or 0), reverse=True)
    elif sort_by == "status":
        # 完成度高在前
        out.sort(key=lambda r: r.get("doneCount") or 0, reverse=True)
    else:
        out.sort(key=lambda r: r.get("orderDate") or "", reverse=True)

    total_filtered = len(out)
    start = max(0, (page - 1) * page_size)
    paged = out[start:start + page_size]

    # 按时间标签分组（保持排序：第一次出现的 label 顺序即为排序结果）
    group_order: list[str] = []
    groups: dict[str, list[dict]] = {}
    for item in paged:
        label = _time_group_label(_parse_dt(item.get("orderDate")), today)
        if label not in groups:
            group_order.append(label)
            groups[label] = []
        groups[label].append(item)

    return {
        "total": total_filtered,
        "page": page,
        "pageSize": page_size,
        # 催票总额：当前筛选结果里还差多少票没开（采购员要催的合计）。
        "invoiceOutstandingTotal": round(sum(r.get("invoiceOutstanding") or 0 for r in out), 2),
        "groups": [{"label": k, "items": groups[k]} for k in group_order],
    }


def workbench(db: Session, order_id: int) -> dict | None:
    """选中订单的工作面板：5 步骤每步的状态 + 关键明细 + 跳转入口。"""
    pf = ChainPrefetch(db)
    # 工作台专用 ID：文件订单用正数，工作流独有订单用负数，避免两张表主键撞车。
    order: Alibaba1688Order | None = None
    external: ExternalPurchaseOrder | None = None
    for candidate_order, candidate_external in _source_pairs(db):
        candidate_id = candidate_order.id if candidate_order is not None else -candidate_external.id
        if candidate_id == order_id:
            order, external = candidate_order, candidate_external
            break
    if order is None and external is None:
        return None
    if order is None and external is not None:
        row = _order_row(db, None, external, pf=pf)
    else:
        row = _order_row(db, order, external, pf=pf)
    states = _step_states(row)
    supplier = row.get("supplier") or ""
    exception_ids = _exception_order_ids(db)
    consumable_nos = _consumable_source_nos(db)
    return {
        "order": {
            "orderId": order_id,
            "fileOrderId": row.get("fileOrderId"),
            "externalPoId": row.get("externalPoId"),
            "orderNo": row["orderNo"],
            "platform": row.get("platform") or "other",
            "orderKind": _order_kind(row.get("orderNo"), row.get("supplier"), consumable_nos, row.get("orderKindOverride", ""), bool(row.get("consumable"))),
            "orderKindOverride": row.get("orderKindOverride", ""),
            "supplier": supplier,
            "buyer": row.get("buyer"),
            "amount": row.get("amount"),
            "goodsTotal": row.get("goodsTotal"),
            "freight": row.get("freight"),
            "discount": row.get("discount"),
            "paidAmount": row.get("paidAmount"),
            "adjustmentAmount": row.get("adjustmentAmount"),
            "adjustmentNote": row.get("adjustmentNote"),
            "orderDate": row.get("orderDate"),
            "orderStatus": row.get("orderStatus"),
            "purchaseStatus": row.get("purchaseStatus") or "",
            "jackyunPoBypassed": bool(external is not None and (external.raw or {}).get("jackyunPoBypassed")),
            "invoiceStatus": row.get("invoiceStatus") or "none",
            "invoicedAmount": row.get("invoicedAmount"),
            "invoiceOutstanding": row.get("invoiceOutstanding"),
            "title": row.get("title"),
            "hasException": row.get("orderId") in exception_ids or row.get("externalPoId") in exception_ids,
        },
        "stepStates": states,
        "detail": {
            "allocations": row.get("allocations") or [],
            # 1688 原始报文货品明细（编号/品名/数量/单价/收货状态）。
            # 分配行为空时作为「采购内容」的唯一来源，前端据此展示编号。
            "orderItems": row.get("orderItems") or [],
            # 耗材采购 + 收货（本平台，不生成吉客云单据）：已收货即视为内容确认 + 入库完成。
            "consumable": row.get("consumable"),
            "expenses": row.get("expenses") or [],
            "purchaseOrders": row.get("purchaseOrders") or [],
            "poAmountClosure": row.get("poAmountClosure"),
            "inbound": row.get("inbound") or [],
            "invoice": row.get("invoice") or [],
            "settlement": row.get("settlement") or [],
            "unallocatedAmount": row.get("unallocatedAmount"),
        },
        "stepTotal": WORKBENCH_TOTAL,
        "supplierHistory": order_supplier_history(db, supplier, exclude_order_id=row["orderId"]) if supplier else None,
    }


def suppliers(db: Session, limit: int = 200, offset: int = 0) -> dict:
    """供应商聚合列表（管理视角），直接复用 procurement_chain_service 的聚合。"""
    return supplier_summaries(db, limit=limit, offset=offset)


def supplier_workbench(db: Session, supplier_name: str) -> dict | None:
    """单个供应商详情：历史合作 + 常购 SKU + 最近订单。"""
    return supplier_detail(db, supplier_name)
