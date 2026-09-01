from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.purchase import (
    ExternalPurchaseOrder,
    PurchaseAllocationItem,
    PurchaseExtraExpense,
    PurchaseInvoice,
    PurchaseInvoiceLink,
)
from app.services import purchase_service as svc

router = APIRouter(prefix="/purchase", tags=["purchase"])


def _po_or_404(db: Session, po_id: int) -> ExternalPurchaseOrder:
    po = svc.get_po(db, po_id)
    if not po:
        raise HTTPException(404, "采购订单不存在")
    return po


def _balance(db: Session, po: ExternalPurchaseOrder) -> dict[str, Any]:
    bal = svc.balance_of(db, po)
    return {k: (str(v) if hasattr(v, "quantize") else v) for k, v in bal.items()}


class CreatePOBody(BaseModel):
    external_order_id: str = Field(..., min_length=1)
    supplier_name: str = ""
    title: str = ""
    ordered_at: datetime | None = None
    order_amount: str | None = None
    paid_amount: str | None = None
    buyer_account: str = ""


@router.get("/orders")
def list_orders(
    status: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    q = db.query(ExternalPurchaseOrder).order_by(ExternalPurchaseOrder.id.desc())
    if status:
        q = q.filter(ExternalPurchaseOrder.purchase_status == status)
    out = []
    for po in q.limit(limit).offset(offset).all():
        bal = svc.balance_of(db, po)
        out.append({
            "id": po.id, "externalOrderId": po.external_order_id, "supplierName": po.supplier_name,
            "title": po.title, "paidAmount": str(po.paid_amount) if po.paid_amount is not None else None,
            "purchaseStatus": po.purchase_status, "invoiceStatus": po.invoice_status,
            "goodsAllocated": str(bal["goods_allocated"]), "expenseAllocated": str(bal["expense_allocated"]),
            "unallocated": str(bal["unallocated"]), "balanced": bal["balanced"],
        })
    return out


@router.post("/orders")
def create_order(body: CreatePOBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        po = svc.create_external_po(
            db, external_order_id=body.external_order_id, supplier_name=body.supplier_name,
            title=body.title, ordered_at=body.ordered_at, order_amount=body.order_amount,
            paid_amount=body.paid_amount, buyer_account=body.buyer_account,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": po.id, "externalOrderId": po.external_order_id, "purchaseStatus": po.purchase_status}


class AllocationBody(BaseModel):
    sku_id: int | None = None
    sku_code: str = ""
    goods_name: str = ""
    quantity: str
    unit_price: str


@router.post("/orders/{po_id}/allocations")
def add_allocation(po_id: int, body: AllocationBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    po = _po_or_404(db, po_id)
    try:
        row = svc.add_allocation(db, po, sku_id=body.sku_id, sku_code=body.sku_code,
                                 goods_name=body.goods_name, quantity=body.quantity,
                                 unit_price=body.unit_price)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": row.id, "amount": str(row.amount), "balance": _balance(db, po)}


@router.delete("/allocations/{item_id}")
def remove_allocation(po_id: int, item_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    po = _po_or_404(db, po_id)
    try:
        svc.remove_allocation(db, po, item_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "balance": _balance(db, po)}


class ExpenseBody(BaseModel):
    expense_type: str
    amount: str
    note: str = ""


@router.post("/orders/{po_id}/expenses")
def add_expense(po_id: int, body: ExpenseBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    po = _po_or_404(db, po_id)
    try:
        row = svc.add_expense(db, po, expense_type=body.expense_type, amount=body.amount, note=body.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": row.id, "amount": str(row.amount), "balance": _balance(db, po)}


@router.delete("/expenses/{expense_id}")
def remove_expense(po_id: int, expense_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    po = _po_or_404(db, po_id)
    try:
        svc.remove_expense(db, po, expense_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "balance": _balance(db, po)}


@router.get("/orders/{po_id}/balance")
def get_balance(po_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    po = _po_or_404(db, po_id)
    return _balance(db, po)


@router.post("/orders/{po_id}/refine")
def refine(po_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    po = _po_or_404(db, po_id)
    try:
        bal = svc.mark_refined(db, po)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "purchaseStatus": po.purchase_status, "balance": {k: (str(v) if hasattr(v, "quantize") else v) for k, v in bal.items()}}


class StatusBody(BaseModel):
    status: str


@router.post("/orders/{po_id}/status")
def advance(po_id: int, body: StatusBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    po = _po_or_404(db, po_id)
    try:
        svc.advance_status(db, po, body.status)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "purchaseStatus": po.purchase_status}


class JackyunLinkBody(BaseModel):
    purch_no: str


@router.post("/orders/{po_id}/jackyun-link")
def jackyun_link(po_id: int, body: JackyunLinkBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    po = _po_or_404(db, po_id)
    try:
        svc.link_jackyun_po(db, po, body.purch_no)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "purchaseStatus": po.purchase_status}


class InvoiceBody(BaseModel):
    invoice_no: str = ""
    invoice_amount: str
    invoice_date: datetime | None = None
    supplier_name: str = ""


@router.post("/invoices")
def create_invoice(body: InvoiceBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = svc.create_invoice(db, invoice_no=body.invoice_no, invoice_amount=body.invoice_amount,
                             invoice_date=body.invoice_date, supplier_name=body.supplier_name)
    return {"id": row.id, "invoiceNo": row.invoice_no, "invoiceAmount": str(row.invoice_amount)}


class InvoiceLinkBody(BaseModel):
    po_id: int
    allocated_amount: str


@router.post("/invoices/{invoice_id}/links")
def link_invoice(invoice_id: int, body: InvoiceLinkBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    invoice = db.get(PurchaseInvoice, invoice_id)
    if not invoice:
        raise HTTPException(404, "发票不存在")
    po = _po_or_404(db, body.po_id)
    try:
        svc.link_invoice(db, invoice, po, body.allocated_amount)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "poInvoiceStatus": po.invoice_status}


class InvoiceStatusBody(BaseModel):
    status: str


@router.post("/orders/{po_id}/invoice-status")
def set_invoice_status(po_id: int, body: InvoiceStatusBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    po = _po_or_404(db, po_id)
    try:
        svc.set_invoice_status(db, po, body.status)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "invoiceStatus": po.invoice_status}


@router.get("/orders/{po_id}")
def order_detail(po_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    po = _po_or_404(db, po_id)
    items = db.query(PurchaseAllocationItem).filter_by(po_id=po.id).all()
    expenses = db.query(PurchaseExtraExpense).filter_by(po_id=po.id).all()
    links = db.query(PurchaseInvoiceLink).filter_by(po_id=po.id).all()
    invoices = []
    for l in links:
        inv = db.get(PurchaseInvoice, l.invoice_id)
        if inv:
            invoices.append({
                "id": inv.id, "invoiceNo": inv.invoice_no,
                "invoiceAmount": str(inv.invoice_amount) if inv.invoice_amount is not None else None,
                "allocatedAmount": str(l.allocated_amount) if l.allocated_amount is not None else None,
                "invoiceDate": inv.invoice_date.isoformat() if inv.invoice_date else None,
            })
    return {
        "id": po.id, "externalOrderId": po.external_order_id, "platform": po.platform,
        "supplierName": po.supplier_name, "title": po.title,
        "orderAmount": str(po.order_amount) if po.order_amount is not None else None,
        "paidAmount": str(po.paid_amount) if po.paid_amount is not None else None,
        "orderedAt": po.ordered_at.isoformat() if po.ordered_at else None,
        "purchaseStatus": po.purchase_status, "invoiceStatus": po.invoice_status,
        "allocations": [
            {"id": i.id, "skuId": i.sku_id, "skuCode": i.sku_code, "goodsName": i.goods_name,
             "quantity": str(i.quantity) if i.quantity is not None else None,
             "unitPrice": str(i.unit_price) if i.unit_price is not None else None,
             "amount": str(i.amount) if i.amount is not None else None}
            for i in items
        ],
        "expenses": [
            {"id": e.id, "expenseType": e.expense_type,
             "amount": str(e.amount) if e.amount is not None else None, "note": e.note}
            for e in expenses
        ],
        "invoices": invoices,
        "balance": _balance(db, po),
    }
