"""采购中心服务：外部采购订单 → 完善采购 → 状态机 → 发票。

- external_order_id 幂等（规格 16）：重复登记/同步只更新状态
- 金额分配：未分配 ≠ 0 禁止标记"采购内容完整"（规格 7.4）
- 采购状态与发票状态分离（规格 7.5）
- 发票金额超采购 / 一票多单分配不平 → 异常中心（规格 12）
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.audit import audit
from app.models.catalog import ProductSku
from app.models.ops import ExceptionRecord
from app.models.purchase import (
    ExternalPurchaseOrder,
    JackyunPurchaseOrder,
    JackyunPurchaseOrderLink,
    PurchaseAllocationItem,
    PurchaseExtraExpense,
    PurchaseInvoice,
    PurchaseInvoiceLink,
)
from app.services.allocation import balance_check
from app.utils.money import money_sum, to_decimal

EXPENSE_TYPES = {"pack", "processing", "plate", "mold", "freight", "testing", "other"}

# 采购订单号统一落在 external_purchase_orders；平台只描述订单来源，
# 不要求该订单一定能回到 1688 原始文件。
PURCHASE_PLATFORMS = {"1688", "pdd", "taobao", "other"}


def normalize_platform(value: str | None) -> str:
    """把前端/导入文件里的渠道名归一为采购链路使用的短值。"""
    raw = str(value or "1688").strip().lower()
    aliases = {
        "拼多多": "pdd", "pinduoduo": "pdd", "淘宝": "taobao", "天猫": "taobao",
        "手工": "other", "手工登记": "other", "manual": "other", "其他": "other", "线下": "other",
    }
    platform = aliases.get(raw, raw)
    if platform not in PURCHASE_PLATFORMS:
        raise ValueError("采购渠道仅支持 1688、拼多多、淘宝或其他")
    return platform

# 采购状态机（规格 7.5）
PURCHASE_FLOW = [
    "pending_refine",   # 待完善
    "confirmed",        # 已确认采购内容
    "jackyun_linked",   # 已关联/生成吉客云采购单
    "producing",        # 待发货/生产中
    "shipped",          # 已发货
    "arrived",          # 已到货
    "inbound",          # 已入库
    "done",             # 完成
]
INVOICE_FLOW = {"unverified", "none", "applied", "partial", "full"}


def validate_transition(current: str, nxt: str) -> bool:
    if current not in PURCHASE_FLOW or nxt not in PURCHASE_FLOW:
        return False
    return PURCHASE_FLOW.index(nxt) == PURCHASE_FLOW.index(current) + 1


def derive_invoice_status(paid, linked_total, manual_status: str) -> str:
    paid_d = to_decimal(paid)
    total_d = to_decimal(linked_total)
    if total_d <= 0:
        return manual_status if manual_status in ("unverified", "none") else "unverified"
    if paid_d > 0 and total_d >= paid_d:
        return "full"
    return "partial"


def _ensure_exception(db: Session, code: str, title: str, detail: str) -> None:
    exists = (
        db.query(ExceptionRecord)
        .filter(ExceptionRecord.code == code, ExceptionRecord.status.in_(("pending", "confirmed")))
        .first()
    )
    if exists:
        exists.detail = {"latest": detail[:2000]}
    else:
        db.add(ExceptionRecord(code=code, type=code, title=title,
                               detail={"latest": detail[:2000]}, severity="high", source="system"))


def create_external_po(db: Session, *, external_order_id: str, supplier_name: str = "",
                       title: str = "", ordered_at=None, order_amount=None, paid_amount=None,
                       buyer_account: str = "", platform: str = "1688", raw: dict | None = None,
                       actor: str = "system") -> ExternalPurchaseOrder:
    """登记/同步一笔外部采购订单。幂等：已存在则更新状态字段，绝不重复生成。"""
    platform = normalize_platform(platform)
    po = db.query(ExternalPurchaseOrder).filter_by(external_order_id=external_order_id).first()
    if po:
        po.order_status = po.order_status or ""
        po.synced_at = datetime.now(timezone.utc)
        if raw:
            po.raw = {**(po.raw or {}), **raw}
        db.commit()
        # 历史入库链可能早于工作流 PO 建立；再次登记时补齐入库明细 SKU。
        from app.services.inbound_allocation_seed import seed_allocations_for_po
        seed_allocations_for_po(db, po)
        return po
    po = ExternalPurchaseOrder(
        external_order_id=external_order_id,
        platform=platform,
        supplier_name=supplier_name or "",
        title=title or "",
        ordered_at=ordered_at,
        order_amount=to_decimal(order_amount) if order_amount is not None else None,
        paid_amount=to_decimal(paid_amount) if paid_amount is not None else None,
        buyer_account=buyer_account,
        synced_at=datetime.now(timezone.utc),
        raw=raw or {},
    )
    db.add(po)
    db.commit()
    audit(db, actor, "purchase.po.create", "external_purchase_orders", po.id,
          {"externalOrderId": external_order_id})
    # 若入库单已通过文件/对照表关联，工作流创建后立即带出待分配 SKU。
    from app.services.inbound_allocation_seed import seed_allocations_for_po
    seed_allocations_for_po(db, po)
    return po


def update_external_po(
    db: Session,
    po: ExternalPurchaseOrder,
    *,
    platform: str | None = None,
    supplier_name: str | None = None,
    title: str | None = None,
    ordered_at=None,
    order_amount=None,
    paid_amount=None,
    buyer_account: str | None = None,
    actor: str = "system",
) -> ExternalPurchaseOrder:
    """维护订单主档字段；订单号本身保持不变，避免破坏已建立的链路。"""
    if platform is not None:
        po.platform = normalize_platform(platform)
    if supplier_name is not None:
        po.supplier_name = supplier_name.strip()
    if title is not None:
        po.title = title.strip()
    if ordered_at is not None:
        po.ordered_at = ordered_at
    if order_amount is not None:
        value = to_decimal(order_amount)
        if not value.is_finite() or value < 0:
            raise ValueError("订单金额必须是非负数字")
        po.order_amount = value
    if paid_amount is not None:
        value = to_decimal(paid_amount)
        if not value.is_finite() or value < 0:
            raise ValueError("实付金额必须是非负数字")
        po.paid_amount = value
    if buyer_account is not None:
        po.buyer_account = buyer_account.strip()
    db.commit()
    audit(db, actor, "purchase.po.update", "external_purchase_orders", po.id,
          {"orderNo": po.external_order_id, "platform": po.platform})
    return po


def get_po(db: Session, po_id: int) -> ExternalPurchaseOrder | None:
    return db.get(ExternalPurchaseOrder, po_id)


def balance_of(db: Session, po: ExternalPurchaseOrder) -> dict[str, Any]:
    items = db.query(PurchaseAllocationItem).filter_by(po_id=po.id).all()
    expenses = db.query(PurchaseExtraExpense).filter_by(po_id=po.id).all()
    return balance_check(
        po.effective_paid_amount,
        [i.amount for i in items],
        [e.amount for e in expenses],
    )


def add_allocation(db: Session, po: ExternalPurchaseOrder, *, sku_id: int | None,
                   sku_code: str, goods_name: str, quantity, unit_price,
                   note: str = "", actor: str = "system", allocation_id: int | None = None) -> PurchaseAllocationItem:
    if po.purchase_status != "pending_refine":
        raise ValueError("仅“待完善”状态可修改分配")
    if not sku_id:
        raise ValueError("必须从吉客云 SKU 主档中选择商品")
    sku = db.get(ProductSku, sku_id)
    if sku is None:
        raise ValueError("SKU 不存在，请先同步吉客云商品主档")
    qty = to_decimal(quantity)
    price = to_decimal(unit_price)
    if not qty.is_finite() or not price.is_finite() or qty <= 0 or price < 0:
        raise ValueError("数量/单价非法")
    row = db.get(PurchaseAllocationItem, allocation_id) if allocation_id is not None else PurchaseAllocationItem(po_id=po.id)
    if row is None or row.po_id != po.id:
        raise ValueError("该 SKU 分配不属于当前订单")
    before = {"skuId": row.sku_id, "quantity": str(row.quantity), "unitPrice": str(row.unit_price)} if allocation_id else None
    was_inbound_auto = allocation_id is not None and row.source == "inbound_auto"
    row.sku_id, row.sku_code, row.goods_name = sku.id, sku.sku_code, sku.sku_name
    row.quantity, row.unit_price = qty, price
    row.amount = (qty * price).quantize(Decimal("0.0001"))
    row.source = "manual"
    row.match_confidence = None
    # 仅新建时写入归属备注（如「由入库单 #N 明细自动反填」），编辑时保留原备注不覆盖，
    # 避免把入库单自动反填行的归属弄丢。
    if allocation_id is None and note:
        row.note = note
    db.add(row)
    db.commit()
    audit(db, actor, "purchase.allocation.update" if allocation_id else "purchase.allocation.add", "purchase_allocation_items", row.id,
          {"poId": po.id, "skuCode": sku.sku_code, "amount": str(row.amount), "before": before})
    # 分配行原为入库单反填时，把人工修正回写入库单明细并重算单据金额（源头更正）
    if was_inbound_auto:
        try:
            from app.services.inbound_allocation_seed import sync_allocation_to_inbound
            sync_result = sync_allocation_to_inbound(db, po, row, actor=actor, was_inbound_auto=True)
            if sync_result.get("synced", 0) > 0:
                db.commit()
        except Exception:  # 回写失败不影响分配保存本身
            db.rollback()
            db.add(row)
            db.commit()
    return row


def remove_allocation(db: Session, po: ExternalPurchaseOrder, item_id: int,
                      actor: str = "system") -> None:
    if po.purchase_status != "pending_refine":
        raise ValueError("仅“待完善”状态可修改分配")
    row = db.get(PurchaseAllocationItem, item_id)
    if row and row.po_id == po.id:
        detail = {"poId": po.id, "skuCode": row.sku_code}
        db.delete(row)
        db.commit()
        audit(db, actor, "purchase.allocation.delete", "purchase_allocation_items",
              item_id, detail)


def add_expense(db: Session, po: ExternalPurchaseOrder, *, expense_type: str, amount,
                note: str = "", actor: str = "system") -> PurchaseExtraExpense:
    if po.purchase_status != "pending_refine":
        raise ValueError("仅“待完善”状态可修改费用")
    if expense_type not in EXPENSE_TYPES:
        raise ValueError(f"非法费用类型: {expense_type}")
    amt = to_decimal(amount)
    if amt <= 0:
        raise ValueError("费用金额必须大于 0")
    row = PurchaseExtraExpense(po_id=po.id, expense_type=expense_type, amount=amt, note=note)
    db.add(row)
    db.commit()
    audit(db, actor, "purchase.expense.add", "purchase_extra_expenses", row.id,
          {"poId": po.id, "type": expense_type, "amount": str(amt)})
    return row


def set_adjustment(db: Session, po: ExternalPurchaseOrder, *, amount,
                   note: str = "", actor: str = "system") -> None:
    """1688 微调金额：红包等导致开票金额（准确）与订单实付的零头差。

    分配平衡目标变为 实付 + 微调；传空清除微调。任意采购状态均可设置（开票对账常发生在确认之后）。
    """
    text = str(amount or "").strip()
    if text == "":
        value = None
    else:
        value = to_decimal(text)
        if value is None or not value.is_finite():
            raise ValueError("微调金额非法")
        if value == 0:
            value = None
    before = str(po.adjustment_amount) if po.adjustment_amount is not None else None
    po.adjustment_amount = value
    po.adjustment_note = (note or "").strip() if value is not None else ""
    db.commit()
    audit(db, actor, "purchase.po.adjustment", "external_purchase_orders", po.id,
          {"before": before, "after": str(value) if value is not None else None, "note": po.adjustment_note})


def remove_expense(db: Session, po: ExternalPurchaseOrder, expense_id: int,
                   actor: str = "system") -> None:
    if po.purchase_status != "pending_refine":
        raise ValueError("仅“待完善”状态可修改费用")
    row = db.get(PurchaseExtraExpense, expense_id)
    if row and row.po_id == po.id:
        detail = {"poId": po.id, "type": row.expense_type, "amount": str(row.amount)}
        db.delete(row)
        db.commit()
        audit(db, actor, "purchase.expense.delete", "purchase_extra_expenses",
              expense_id, detail)


def mark_refined(db: Session, po: ExternalPurchaseOrder,
                 actor: str = "system") -> dict[str, Any]:
    """标记“采购内容完整”。未分配 ≠ 0 直接拒绝（规格 7.4）。"""
    bal = balance_of(db, po)
    if not bal["allow_mark_refined"]:
        _ensure_exception(db, "PURCHASE_UNBALANCED", "采购金额分配不平",
                          f"PO {po.external_order_id} 未分配 {bal['unallocated']}")
        db.commit()
        raise ValueError(f"未分配金额 {bal['unallocated']} ≠ 0，禁止标记采购内容完整")
    from app.services.cost_policy import allocation_cost_anomalies
    cost_anomalies = allocation_cost_anomalies(db, po)
    if cost_anomalies:
        _ensure_exception(db, "PURCHASE_COST_MISMATCH", "固定成本与采购单价不一致", "；".join(cost_anomalies))
        db.commit()
        raise ValueError("固定成本异常，需人工确认：" + "；".join(cost_anomalies[:3]))
    if po.purchase_status != "pending_refine":
        raise ValueError("当前状态不允许该操作")
    po.purchase_status = "confirmed"
    po.refined_at = datetime.now(timezone.utc)
    po.allocated_goods_amount = bal["goods_allocated"]
    po.allocated_expense_amount = bal["expense_allocated"]
    db.commit()
    audit(db, actor, "purchase.po.refined", "external_purchase_orders", po.id,
          {"goods": str(bal["goods_allocated"]), "expense": str(bal["expense_allocated"])})
    return bal


def advance_status(db: Session, po: ExternalPurchaseOrder, nxt: str,
                   actor: str = "system") -> None:
    if not validate_transition(po.purchase_status, nxt):
        raise ValueError(f"非法状态流转: {po.purchase_status} → {nxt}")
    if nxt == "confirmed":
        mark_refined(db, po, actor=actor)
        return
    if nxt == "jackyun_linked":
        has_link = db.query(JackyunPurchaseOrderLink).filter_by(po_id=po.id).first()
        if not has_link and not _jackyun_po_bypassed(po):
            raise ValueError("请先关联真实吉客云采购单（或按 Excel 口径跳过采购单步骤），不能仅修改状态")
    if nxt in ("inbound", "done"):
        from app.models.alibaba1688_import import Alibaba1688Order
        from app.services.procurement_chain_service import _order_row
        source = db.query(Alibaba1688Order).filter_by(external_order_id=po.external_order_id).first()
        row = _order_row(db, source, po)
        if not row["inbound"]:
            raise ValueError("请先关联真实采购入库单")
        if any(not inbound.get("consumableUsageDecided") for inbound in row["inbound"]):
            raise ValueError("请先逐张入库单确认是否添加耗材使用及数量")
        if nxt == "done" and not (row["invoice"] and row["verified"] and any(s["paid"] for s in row["settlement"])):
            raise ValueError("完成前须核实发票、付款关联及税务认证记录")
    po.purchase_status = nxt
    db.commit()
    audit(db, actor, "purchase.po.status", "external_purchase_orders", po.id, {"to": nxt})


def reopen_purchase(db: Session, po: ExternalPurchaseOrder, actor: str = "system") -> None:
    before = po.purchase_status
    po.purchase_status = "pending_refine"
    po.refined_at = None
    po.allocated_goods_amount = None
    po.allocated_expense_amount = None
    db.commit()
    audit(db, actor, "purchase.po.reopen", "external_purchase_orders", po.id,
          {"from": before, "to": po.purchase_status, "note": "人工重新编辑，保留已关联原始单据"})


def link_jackyun_po(db: Session, po: ExternalPurchaseOrder, purch_no: str,
                    actor: str = "system", replace_link_id: int | None = None,
                    relation_kind: str = "", alloc_amount=None, note: str = "") -> JackyunPurchaseOrderLink:
    """关联吉客云采购单（按采购单号登记；Phase 1 同步落地后自动匹配）。

    合并/拆分场景（N:1 或 1:N）通过 relation_kind 标注：
    - merged：多张 1688 单共用一张吉客云采购单；
    - split：一张 1688 单拆成多张吉客云采购单；
    标注时建议同时填 alloc_amount（本订单在该采购单中的分摊金额），用于金额闭环核对。
    """
    if not purch_no:
        raise ValueError("吉客云采购单号不能为空")
    if relation_kind not in ("", "merged", "split"):
        raise ValueError(f"非法关联类型: {relation_kind}")
    jpo = db.query(JackyunPurchaseOrder).filter_by(purch_no=purch_no).first()
    if not jpo:
        raise ValueError("吉客云采购单不存在，请先完成吉客云采购单同步")
    exists = db.query(JackyunPurchaseOrderLink).filter_by(po_id=po.id, jackyun_po_id=jpo.id).first()
    if exists and exists.id != replace_link_id:
        raise ValueError("该吉客云采购单已关联")
    alloc = to_decimal(alloc_amount) if alloc_amount is not None else None
    if alloc is not None and (not alloc.is_finite() or alloc < 0):
        raise ValueError("分摊金额非法")
    link = db.get(JackyunPurchaseOrderLink, replace_link_id) if replace_link_id else JackyunPurchaseOrderLink(po_id=po.id)
    if link is None or link.po_id != po.id:
        raise ValueError("采购单关联不属于当前订单")
    previous = link.jackyun_po_id
    link.jackyun_po_id = jpo.id
    link.relation_kind = relation_kind
    link.alloc_amount = alloc
    link.note = note or ""
    db.add(link)
    db.commit()
    audit(db, actor, "purchase.po.jackyun_link", "jackyun_purchase_order_links", link.id,
          {"poId": po.id, "purchNo": purch_no, "previousJackyunPoId": previous,
           "relationKind": relation_kind, "allocAmount": str(alloc) if alloc is not None else None})
    if po.purchase_status == "confirmed":
        po.purchase_status = "jackyun_linked"
        db.commit()
    return link


# ---------- 吉客云采购单自动关联（文件导入后 Phase-1 自动匹配） ----------

def _jackyun_po_date(jpo: JackyunPurchaseOrder):
    """吉客云采购单的业务日期：优先 raw.date（文件导入/接口同步写入），缺省回退建档时间。"""
    raw = jpo.raw or {}
    text = raw.get("date")
    if isinstance(text, str) and text.strip():
        cleaned = text.strip()[:24]
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
            try:
                return datetime.strptime(cleaned, fmt)
            except ValueError:
                continue
    return jpo.created_at


def auto_link_purchase_orders(db: Session, actor: str = "system", dry_run: bool = False) -> dict:
    """把已导入的吉客云采购单自动关联到已确认的 1688 采购订单（Phase-1 自动匹配）。

    匹配口径与发票/结算启发式一致：供应商归一化 + 金额 2% 容差 + 45 天时间窗，
    候选唯一时才自动关联；合并/拆分等一对多场景金额与订单实付不吻合，天然留待人工标注。

    - 只处理：platform == 1688、purchase_status == confirmed、尚无采购单关联的订单；
    - 关联即推进 confirmed -> jackyun_linked（与工作台手动「关联采购单」同语义）；
    - 幂等：已关联过的采购单/订单自动跳过，可重复触发；
    - dry_run=True 只计算候选不落库、不改状态，用于预览/测试。
    """
    from app.models.alibaba1688_import import Alibaba1688FileImport, Alibaba1688Order
    from app.services.import_lifecycle import filter_active_import, filter_active_rows
    from app.services.procurement_chain_service import (  # noqa: E402
        MATCH_WINDOW_DAYS,
        _days_apart,
        amount_close,
        normalize_name,
        parse_decimal,
    )

    result: dict = {"ok": True, "dryRun": dry_run, "errors": []}

    # 候选采购单：已导入且未关联任何订单。
    all_links = db.query(JackyunPurchaseOrderLink).all()
    linked_jpo_ids = {l.jackyun_po_id for l in all_links}
    linked_order_ids = {l.po_id for l in all_links}
    jpos = [
        jpo for jpo in db.query(JackyunPurchaseOrder).order_by(JackyunPurchaseOrder.id).all()
        if jpo.id not in linked_jpo_ids
    ]
    result["jpoTotal"] = len(jpos)

    # 候选订单：1688 渠道、已确认内容、无采购单关联；供应商/金额/日期齐备。
    active_sources = {
        o.external_order_id: o
        for o in filter_active_rows(
            filter_active_import(
                db.query(Alibaba1688Order),
                Alibaba1688Order, Alibaba1688FileImport, Alibaba1688Order.import_id,
            ),
            Alibaba1688Order,
        ).all()
    }
    order_rows = db.query(ExternalPurchaseOrder).filter(
        ExternalPurchaseOrder.platform == "1688",
        ExternalPurchaseOrder.purchase_status == "confirmed",
    ).all()
    orders: list[tuple[ExternalPurchaseOrder, Alibaba1688Order, str, Decimal, object]] = []
    for ext in order_rows:
        if ext.id in linked_order_ids:
            continue
        src = active_sources.get(ext.external_order_id)
        if src is None:
            continue
        supplier = normalize_name(src.seller_company_name or ext.supplier_name)
        amount = ext.effective_paid_amount
        if not supplier or amount is None:
            continue
        orders.append((ext, src, supplier, amount, ext.ordered_at or src.order_time))
    result["ordersScanned"] = len(orders)

    matched_pairs: list[dict] = []
    ambiguous: list[dict] = []
    linked_count = 0
    claimed_order_ids: set[int] = set()
    for jpo in jpos:
        supplier = normalize_name(jpo.supplier_name)
        amount = parse_decimal(jpo.amount)
        if not supplier:
            continue
        if amount is None or amount <= 0:
            continue
        jpo_date = _jackyun_po_date(jpo)
        if jpo_date is None:
            continue

        candidates: list[tuple[float, Decimal, ExternalPurchaseOrder, Alibaba1688Order]] = []
        for ext, src, order_supplier, paid, ordered_at in orders:
            if ext.id in claimed_order_ids:
                continue
            if order_supplier != supplier:
                continue
            if not amount_close(paid, amount):
                continue
            days = _days_apart(ordered_at, jpo_date)
            if days is None or days > MATCH_WINDOW_DAYS:
                continue
            candidates.append((days, abs(paid - amount), ext, src))
        if not candidates:
            continue
        candidates.sort(key=lambda item: (item[0], item[1], item[2].id))
        days, diff, ext, src = candidates[0]
        if len(candidates) > 1 and abs(candidates[1][1] - diff) < Decimal("0.01"):
            ambiguous.append({
                "jackyunPoId": jpo.id,
                "purchNo": jpo.purch_no or jpo.jackyun_purch_id,
                "amount": str(amount),
                "candidates": len(candidates),
            })
            continue
        claimed_order_ids.add(ext.id)
        note = f"自动匹配：供应商+金额一致，时间相距 {days:.0f} 天"
        pair = {
            "poId": ext.id,
            "orderNo": ext.external_order_id,
            "orderAmount": str(ext.effective_paid_amount),
            "jackyunPoId": jpo.id,
            "purchNo": jpo.purch_no or jpo.jackyun_purch_id,
            "jackyunPoAmount": str(amount),
            "days": round(days, 1),
            "note": note,
        }
        matched_pairs.append(pair)
        if dry_run:
            continue
        try:
            link = JackyunPurchaseOrderLink(
                po_id=ext.id, jackyun_po_id=jpo.id,
                relation_kind="", alloc_amount=None, note=note,
            )
            db.add(link)
            if ext.purchase_status == "confirmed":
                ext.purchase_status = "jackyun_linked"
            audit(db, actor, "purchase.po.auto_link", "jackyun_purchase_order_links",
                  jpo.id, {"poId": ext.id, "orderNo": ext.external_order_id,
                           "purchNo": jpo.purch_no or jpo.jackyun_purch_id})
            db.commit()
            linked_count += 1
        except Exception as exc:
            db.rollback()
            result["errors"].append(f"{jpo.purch_no or jpo.jackyun_purch_id} 关联失败：{exc}")

    result["matched"] = len(matched_pairs)
    result["linked"] = linked_count
    result["advanced"] = linked_count if not dry_run else 0
    result["matchedPairs"] = matched_pairs
    result["ambiguous"] = ambiguous
    return result


# ---------- 采购单步骤按 Excel 口径跳过（吉客云无采购单体系，2026-09-06 用户口径） ----------
# 金额维度以用户导入表格为准；入库已闭环时，吉客云采购单缺失不再卡住「待生成采购单」。
# 留痕 external.raw.jackyunPoBypassed + 审计 purchase.po.bypass_jackyun_po；日后吉客云补建采购单
# 可解除跳过（置回 confirmed）再走 auto_link_purchase_orders 正常关联。

JACKYUN_PO_BYPASS_KEY = "jackyunPoBypassed"


def _jackyun_po_bypassed(po: ExternalPurchaseOrder) -> bool:
    return bool((po.raw or {}).get(JACKYUN_PO_BYPASS_KEY))


def clear_jackyun_po_bypass(db: Session, po: ExternalPurchaseOrder, actor: str = "system") -> bool:
    """解除采购单步骤跳过标记（保留状态；需人工流转回 confirmed 再重新确认/关联）。"""
    raw = dict(po.raw or {})
    if JACKYUN_PO_BYPASS_KEY not in raw:
        return False
    raw.pop(JACKYUN_PO_BYPASS_KEY, None)
    po.raw = raw
    db.commit()
    audit(db, actor, "purchase.po.clear_jackyun_po_bypass", "external_purchase_orders", po.id,
          {"orderNo": po.external_order_id})
    return True


def _inbound_links_for_po(db: Session, po: ExternalPurchaseOrder):
    """已确认关联的入库单链路：1688 原件走 order_id 桥接；工作流独有单走 external_po_id。"""
    from sqlalchemy import or_

    from app.models.alibaba1688_import import Alibaba1688Order
    from app.models.procurement_chain import ProcurementChainLink

    source = db.query(Alibaba1688Order).filter_by(external_order_id=po.external_order_id).first()
    query = db.query(ProcurementChainLink).filter(
        ProcurementChainLink.target_type == "inbound",
        ProcurementChainLink.confirmed.is_(True),
    )
    if source is not None:
        query = query.filter(or_(
            ProcurementChainLink.order_id == source.id,
            ProcurementChainLink.external_po_id == po.id,
        ))
    else:
        query = query.filter(ProcurementChainLink.external_po_id == po.id)
    return query.all()


def _po_amount_closed_for_bypass(db: Session, po: ExternalPurchaseOrder) -> bool:
    """金额按 Excel/分配口径闭环：实付+微调 vs 分配商品+附加费用（2% 或 0.05 容差）。"""
    target = po.effective_paid_amount
    if target is None:
        return False
    goods = sum((a.amount or Decimal("0")) for a in db.query(PurchaseAllocationItem).filter_by(po_id=po.id).all())
    expenses = sum((e.amount or Decimal("0")) for e in db.query(PurchaseExtraExpense).filter_by(po_id=po.id).all())
    total = goods + expenses
    return abs(total - target) <= max(abs(target) * Decimal("0.02"), Decimal("0.05"))


def bypass_jackyun_po_inbound_closed(db: Session, actor: str = "system", dry_run: bool = False) -> dict:
    """批量放行「待生成采购单」订单（Excel 金额口径）。

    候选：platform=1688、purchase_status=confirmed、无吉客云采购单关联、未跳过过、
    已关联确认入库单、分配+费用金额与实付闭环。放行即 confirmed -> jackyun_linked。
    """
    result: dict = {"ok": True, "dryRun": dry_run, "bypassed": [], "skipped": [], "errors": []}
    candidates = db.query(ExternalPurchaseOrder).filter(
        ExternalPurchaseOrder.platform == "1688",
        ExternalPurchaseOrder.purchase_status == "confirmed",
    ).all()
    for po in candidates:
        order_no = po.external_order_id
        if db.query(JackyunPurchaseOrderLink).filter_by(po_id=po.id).first():
            continue  # 已真实关联采购单
        if _jackyun_po_bypassed(po):
            continue
        if not _inbound_links_for_po(db, po):
            result["skipped"].append({"orderNo": order_no, "reason": "尚未关联确认入库单"})
            continue
        if not _po_amount_closed_for_bypass(db, po):
            result["skipped"].append({"orderNo": order_no, "reason": "分配+费用金额与实付未闭环"})
            continue
        result["bypassed"].append({"poId": po.id, "orderNo": order_no})
        if dry_run:
            continue
        try:
            reason = "金额以导入表格为准、入库已闭环；吉客云侧无采购单，采购单步骤按口径放行"
            raw = dict(po.raw or {})
            raw[JACKYUN_PO_BYPASS_KEY] = {
                "at": datetime.now(timezone.utc).isoformat(),
                "actor": actor,
                "reason": reason,
            }
            po.raw = raw
            po.purchase_status = "jackyun_linked"
            db.commit()
            audit(db, actor, "purchase.po.bypass_jackyun_po", "external_purchase_orders", po.id,
                  {"orderNo": order_no, "reason": reason})
            db.commit()
        except Exception as exc:
            db.rollback()
            result["errors"].append(f"{order_no}: {exc}")
    result["matched"] = len(result["bypassed"])
    return result


# ---------- 发票（规格 7.5：与采购状态分离，多对多） ----------

def register_order_invoice(db: Session, po: ExternalPurchaseOrder, *, invoice_no: str,
                           invoice_amount, allocated_amount=None, invoice_date=None,
                           supplier_name: str = "", actor: str = "system") -> PurchaseInvoiceLink:
    """在一个事务内登记并分摊已有发票；校验失败不留下孤立发票。"""
    total = to_decimal(invoice_amount)
    alloc = to_decimal(allocated_amount if allocated_amount is not None else invoice_amount)
    if not total.is_finite() or not alloc.is_finite() or total <= 0 or alloc <= 0 or alloc > total:
        raise ValueError("票面和本单分摊金额须大于 0，分摊不得超过票面")
    invoice_no = invoice_no.strip()
    if not invoice_no:
        raise ValueError("请填写真实发票号码")
    # 锁定订单，避免并发登记将同一订单超额分摊。
    db.refresh(po, with_for_update=True)
    invoice = db.query(PurchaseInvoice).filter_by(invoice_no=invoice_no).with_for_update().first()
    if invoice and invoice.invoice_amount != total:
        raise ValueError("同号码发票的票面金额不一致，请核对原票")
    if invoice and db.query(PurchaseInvoiceLink).filter_by(invoice_id=invoice.id, po_id=po.id).first():
        raise ValueError("该发票已关联当前订单，请先解除错误关联")
    existing_total = money_sum(l.allocated_amount for l in db.query(PurchaseInvoiceLink).filter_by(po_id=po.id).all())
    limit = po.paid_amount if po.paid_amount is not None else po.order_amount
    if limit is not None and existing_total + alloc > limit:
        raise ValueError("本单累计发票分摊超过采购总额")
    if invoice:
        used = money_sum(l.allocated_amount for l in db.query(PurchaseInvoiceLink).filter_by(invoice_id=invoice.id).all())
        if used + alloc > total:
            raise ValueError("发票累计分摊超过票面金额")
    else:
        invoice = PurchaseInvoice(invoice_no=invoice_no, invoice_amount=total,
                                  invoice_date=invoice_date, supplier_name=supplier_name or po.supplier_name,
                                  status="received")
        db.add(invoice)
        db.flush()
    link = PurchaseInvoiceLink(invoice_id=invoice.id, po_id=po.id, allocated_amount=alloc)
    db.add(link)
    po.invoice_status = derive_invoice_status(limit, existing_total + alloc, po.invoice_status)
    db.commit()
    audit(db, actor, "purchase.invoice.register", "purchase_invoice_links", link.id,
          {"poId": po.id, "invoiceId": invoice.id, "allocated": str(alloc)})
    return link

def create_invoice(db: Session, *, invoice_no: str, invoice_amount, invoice_date=None,
                   supplier_name: str = "", actor: str = "system") -> PurchaseInvoice:
    row = PurchaseInvoice(
        invoice_no=invoice_no or "",
        supplier_name=supplier_name or "",
        invoice_amount=to_decimal(invoice_amount),
        invoice_date=invoice_date,
        status="received",
    )
    db.add(row)
    db.commit()
    audit(db, actor, "purchase.invoice.create", "purchase_invoices", row.id,
          {"invoiceNo": invoice_no, "amount": str(row.invoice_amount)})
    return row


def link_invoice(db: Session, invoice: PurchaseInvoice, po: ExternalPurchaseOrder,
                 allocated_amount, actor: str = "system") -> PurchaseInvoiceLink:
    alloc = to_decimal(allocated_amount)
    if alloc <= 0:
        raise ValueError("分摊金额必须大于 0")
    # 发票金额超采购金额 → 异常 + 拒绝
    if po.paid_amount is not None and alloc > to_decimal(po.paid_amount):
        _ensure_exception(db, "INVOICE_OVER_PO", "发票金额超采购金额",
                          f"PO {po.external_order_id} 分摊 {alloc} > 实付 {po.paid_amount}")
        db.commit()
        raise ValueError(f"分摊金额 {alloc} 超过订单实付 {po.paid_amount}")
    exists = db.query(PurchaseInvoiceLink).filter_by(invoice_id=invoice.id, po_id=po.id).first()
    if exists:
        raise ValueError("该发票已关联此订单")
    # 一票多单分配不平 → 异常 + 拒绝
    linked_sum = money_sum([
        l.allocated_amount for l in db.query(PurchaseInvoiceLink).filter_by(invoice_id=invoice.id).all()
    ] + [alloc])
    if invoice.invoice_amount is not None and linked_sum > to_decimal(invoice.invoice_amount):
        _ensure_exception(db, "INVOICE_ALLOCATION_UNBALANCED", "一票多单分配不平",
                          f"发票 {invoice.invoice_no or invoice.id} 已分摊合计 {linked_sum} > 票面 {invoice.invoice_amount}")
        db.commit()
        raise ValueError(f"分摊合计 {linked_sum} 超过票面金额 {invoice.invoice_amount}")

    link = PurchaseInvoiceLink(invoice_id=invoice.id, po_id=po.id, allocated_amount=alloc)
    db.add(link)
    db.commit()
    # 派生该订单的发票状态
    po_total = money_sum([
        l.allocated_amount
        for l in db.query(PurchaseInvoiceLink).filter_by(po_id=po.id).all()
    ])
    po.invoice_status = derive_invoice_status(po.paid_amount, po_total, po.invoice_status)
    db.commit()
    audit(db, actor, "purchase.invoice.link", "purchase_invoice_links", link.id,
          {"invoiceId": invoice.id, "poId": po.id, "allocated": str(alloc),
           "poInvoiceStatus": po.invoice_status})
    return link


def set_invoice_status(db: Session, po: ExternalPurchaseOrder, status: str,
                       actor: str = "system") -> None:
    if status not in INVOICE_FLOW:
        raise ValueError(f"非法发票状态: {status}")
    po.invoice_status = status
    db.commit()
    audit(db, actor, "purchase.po.invoice_status", "external_purchase_orders", po.id,
          {"to": status})
