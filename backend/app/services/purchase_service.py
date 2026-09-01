"""采购中心服务（规格 7）：1688 订单 → 完善采购 → 状态机 → 发票。

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
                       buyer_account: str = "", raw: dict | None = None) -> ExternalPurchaseOrder:
    """登记/同步一笔 1688 订单。幂等：已存在则更新状态字段，绝不重复生成。"""
    po = db.query(ExternalPurchaseOrder).filter_by(external_order_id=external_order_id).first()
    if po:
        po.order_status = po.order_status or ""
        po.synced_at = datetime.now(timezone.utc)
        if raw:
            po.raw = {**(po.raw or {}), **raw}
        db.commit()
        return po
    po = ExternalPurchaseOrder(
        external_order_id=external_order_id,
        platform="1688",
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
    audit(db, "lan_user", "purchase.po.create", "external_purchase_orders", po.id,
          {"externalOrderId": external_order_id})
    return po


def get_po(db: Session, po_id: int) -> ExternalPurchaseOrder | None:
    return db.get(ExternalPurchaseOrder, po_id)


def balance_of(db: Session, po: ExternalPurchaseOrder) -> dict[str, Any]:
    items = db.query(PurchaseAllocationItem).filter_by(po_id=po.id).all()
    expenses = db.query(PurchaseExtraExpense).filter_by(po_id=po.id).all()
    return balance_check(
        po.paid_amount,
        [i.amount for i in items],
        [e.amount for e in expenses],
    )


def add_allocation(db: Session, po: ExternalPurchaseOrder, *, sku_id: int | None,
                   sku_code: str, goods_name: str, quantity, unit_price) -> PurchaseAllocationItem:
    if po.purchase_status != "pending_refine":
        raise ValueError("仅“待完善”状态可修改分配")
    qty = to_decimal(quantity)
    price = to_decimal(unit_price)
    if qty <= 0 or price < 0:
        raise ValueError("数量/单价非法")
    row = PurchaseAllocationItem(
        po_id=po.id, sku_id=sku_id, sku_code=sku_code or "", goods_name=goods_name or "",
        quantity=qty, unit_price=price, amount=(qty * price).quantize(Decimal("0.0001")),
    )
    db.add(row)
    db.commit()
    audit(db, "lan_user", "purchase.allocation.add", "purchase_allocation_items", row.id,
          {"poId": po.id, "skuCode": sku_code, "amount": str(row.amount)})
    return row


def remove_allocation(db: Session, po: ExternalPurchaseOrder, item_id: int) -> None:
    if po.purchase_status != "pending_refine":
        raise ValueError("仅“待完善”状态可修改分配")
    row = db.get(PurchaseAllocationItem, item_id)
    if row and row.po_id == po.id:
        db.delete(row)
        db.commit()


def add_expense(db: Session, po: ExternalPurchaseOrder, *, expense_type: str, amount,
                note: str = "") -> PurchaseExtraExpense:
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
    audit(db, "lan_user", "purchase.expense.add", "purchase_extra_expenses", row.id,
          {"poId": po.id, "type": expense_type, "amount": str(amt)})
    return row


def remove_expense(db: Session, po: ExternalPurchaseOrder, expense_id: int) -> None:
    if po.purchase_status != "pending_refine":
        raise ValueError("仅“待完善”状态可修改费用")
    row = db.get(PurchaseExtraExpense, expense_id)
    if row and row.po_id == po.id:
        db.delete(row)
        db.commit()


def mark_refined(db: Session, po: ExternalPurchaseOrder) -> dict[str, Any]:
    """标记“采购内容完整”。未分配 ≠ 0 直接拒绝（规格 7.4）。"""
    bal = balance_of(db, po)
    if not bal["allow_mark_refined"]:
        _ensure_exception(db, "PURCHASE_UNBALANCED", "采购金额分配不平",
                          f"PO {po.external_order_id} 未分配 {bal['unallocated']}")
        db.commit()
        raise ValueError(f"未分配金额 {bal['unallocated']} ≠ 0，禁止标记采购内容完整")
    if po.purchase_status != "pending_refine":
        raise ValueError("当前状态不允许该操作")
    po.purchase_status = "confirmed"
    po.refined_at = datetime.now(timezone.utc)
    po.allocated_goods_amount = bal["goods_allocated"]
    po.allocated_expense_amount = bal["expense_allocated"]
    db.commit()
    audit(db, "lan_user", "purchase.po.refined", "external_purchase_orders", po.id,
          {"goods": str(bal["goods_allocated"]), "expense": str(bal["expense_allocated"])})
    return bal


def advance_status(db: Session, po: ExternalPurchaseOrder, nxt: str) -> None:
    if not validate_transition(po.purchase_status, nxt):
        raise ValueError(f"非法状态流转: {po.purchase_status} → {nxt}")
    po.purchase_status = nxt
    db.commit()
    audit(db, "lan_user", "purchase.po.status", "external_purchase_orders", po.id, {"to": nxt})


def link_jackyun_po(db: Session, po: ExternalPurchaseOrder, purch_no: str) -> JackyunPurchaseOrderLink:
    """关联吉客云采购单（按采购单号登记；Phase 1 同步落地后自动匹配）。"""
    if not purch_no:
        raise ValueError("吉客云采购单号不能为空")
    jpo = db.query(JackyunPurchaseOrder).filter_by(purch_no=purch_no).first()
    if not jpo:
        jpo = JackyunPurchaseOrder(jackyun_purch_id=purch_no, purch_no=purch_no)
        db.add(jpo)
        db.commit()
    exists = db.query(JackyunPurchaseOrderLink).filter_by(po_id=po.id, jackyun_po_id=jpo.id).first()
    if exists:
        raise ValueError("该吉客云采购单已关联")
    link = JackyunPurchaseOrderLink(po_id=po.id, jackyun_po_id=jpo.id)
    db.add(link)
    db.commit()
    audit(db, "lan_user", "purchase.po.jackyun_link", "jackyun_purchase_order_links", link.id,
          {"poId": po.id, "purchNo": purch_no})
    if po.purchase_status == "confirmed":
        po.purchase_status = "jackyun_linked"
        db.commit()
    return link


# ---------- 发票（规格 7.5：与采购状态分离，多对多） ----------

def create_invoice(db: Session, *, invoice_no: str, invoice_amount, invoice_date=None,
                   supplier_name: str = "") -> PurchaseInvoice:
    row = PurchaseInvoice(
        invoice_no=invoice_no or "",
        supplier_name=supplier_name or "",
        invoice_amount=to_decimal(invoice_amount),
        invoice_date=invoice_date,
        status="received",
    )
    db.add(row)
    db.commit()
    audit(db, "lan_user", "purchase.invoice.create", "purchase_invoices", row.id,
          {"invoiceNo": invoice_no, "amount": str(row.invoice_amount)})
    return row


def link_invoice(db: Session, invoice: PurchaseInvoice, po: ExternalPurchaseOrder,
                 allocated_amount) -> PurchaseInvoiceLink:
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
    audit(db, "lan_user", "purchase.invoice.link", "purchase_invoice_links", link.id,
          {"invoiceId": invoice.id, "poId": po.id, "allocated": str(alloc),
           "poInvoiceStatus": po.invoice_status})
    return link


def set_invoice_status(db: Session, po: ExternalPurchaseOrder, status: str) -> None:
    if status not in INVOICE_FLOW:
        raise ValueError(f"非法发票状态: {status}")
    po.invoice_status = status
    db.commit()
    audit(db, "lan_user", "purchase.po.invoice_status", "external_purchase_orders", po.id,
          {"to": status})
