"""采购执行中心 API：5 步骤任务视角，与 /procurement-chain 共用底层数据。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.core.audit import audit
from app.db import get_db
from app.models.alibaba1688_import import Alibaba1688Order
from app.models.procurement_chain import ProcurementChainLink
from app.models.purchase import (
    ExternalPurchaseOrder,
    JackyunPurchaseOrderLink,
    PurchaseAllocationItem,
    PurchaseExtraExpense,
    PurchaseInvoiceLink,
)
from app.services import procurement_workbench_service as service
from app.services.procurement_chain_service import _source_pairs

router = APIRouter(prefix="/procurement-workbench", tags=["采购执行中心"])


@router.get("/funnel")
def funnel(db: Session = Depends(get_db)) -> dict:
    """5 步漏斗（每步 = 已完成该步的订单数）。"""
    return service.funnel(db)


@router.get("/todos")
def todos(db: Session = Depends(get_db)) -> dict:
    """5 个待办计数：按最早未完成步骤归类，加和 = 待处理订单数。"""
    return service.todos(db)


@router.get("/summary")
def summary(db: Session = Depends(get_db)) -> dict:
    """采购工作台六项待办指标。"""
    return service.summary(db)


@router.get("/orders")
def orders(
    status: str = Query("all", pattern="^(all|pending|done|order|content|sku|jackyun_po|closeout|refine|po|inbound|invoice|exception)$"),
    q: str = Query(""),
    sort_by: str = Query("date", pattern="^(date|amount|status|invoice)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """按下单时间倒序、按时间标签分组的订单列表；支持状态筛选与排序。"""
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    return service.list_orders(
        db,
        status=status,
        q=q,
        sort_by=sort_by,
        page=page,
        page_size=page_size,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/invoice-reconciliation")
def invoice_reconciliation(
    supplier: str = Query("", description="只看该供应商（宽松匹配，供供应商画像用）"),
    db: Session = Depends(get_db),
) -> dict:
    """发票维度对账：进项发票按供应商 FIFO 顺序配平采购订单（纯推导，不落库）。"""
    from app.services import invoice_reconciliation

    return invoice_reconciliation.reconcile(db, supplier=supplier or None)


@router.post("/invoice-match")
def create_invoice_match(
    invoice_id: int = Body(...),
    po_id: int = Body(...),
    db: Session = Depends(get_db),
) -> dict:
    """供应商画像手工微调：把采购订单挂到进项发票（与采购链路共用 tax_invoice_links）。

    1688 平台单优先挂 1688 源单，与采购链路口径一致；写 manual 关联后，
    发票对账推导对该票改为"以手工为准"，不再自动 FIFO。
    """
    from decimal import Decimal

    from app.models.tax import TaxInvoice, TaxInvoiceLink
    from app.services.procurement_chain_service import mark_invoice_linked

    inv = db.get(TaxInvoice, invoice_id)
    if inv is None or inv.direction != "input":
        raise HTTPException(status_code=404, detail="进项发票不存在")
    po = db.get(ExternalPurchaseOrder, po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    target_type, target_id = "external_purchase_order", po.id
    if po.platform == "1688":
        src = (
            db.query(Alibaba1688Order)
            .filter(Alibaba1688Order.external_order_id == po.external_order_id)
            .first()
        )
        if src is not None:
            target_type, target_id = "alibaba1688_order", src.id
    link = (
        db.query(TaxInvoiceLink)
        .filter_by(invoice_id=invoice_id, target_type=target_type, target_id=target_id)
        .first()
    )
    if link is None:
        link = TaxInvoiceLink(invoice_id=invoice_id, target_type=target_type, target_id=target_id)
        db.add(link)
    link.match_method = "manual"
    link.confidence = Decimal("1")
    link.confirmed = True
    link.note = "供应商画像手工匹配"
    mark_invoice_linked(inv, link.note)
    db.commit()
    return {"ok": True, "id": link.id, "invoiceId": invoice_id, "poId": po_id}


@router.delete("/invoice-match/{link_id}")
def delete_invoice_match(link_id: int, db: Session = Depends(get_db)) -> dict:
    """解除供应商画像里的手工发票匹配（软删 match_method=rejected，可审计）。"""
    from app.models.tax import TaxInvoiceLink

    link = db.get(TaxInvoiceLink, link_id)
    if link is None or link.match_method != "manual":
        raise HTTPException(status_code=404, detail="手工匹配不存在或已解除")
    link.match_method = "rejected"
    link.confirmed = False
    link.confidence = None
    link.note = "供应商画像解除匹配"
    db.commit()
    return {"ok": True}


class SupplierRenameBody(BaseModel):
    """供应商改名/归一请求体。"""

    old_name: str = Field(..., min_length=1)
    new_name: str = Field(..., min_length=1)


@router.post("/suppliers/rename")
def rename_supplier(body: SupplierRenameBody, request: Request, db: Session = Depends(get_db)) -> dict:
    """供应商改名/归一：统一该供应商全部订单的供应商写法（同步 1688 源单 + 副本，留审计）。"""
    try:
        return service.rename_supplier(
            db, old_name=body.old_name, new_name=body.new_name, actor=current_actor(request)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/orders/{order_id}/workbench")
def order_workbench(order_id: int, db: Session = Depends(get_db)) -> dict:
    """选中订单的工作面板（5 步状态 + 关键明细 + 供应商历史）。"""
    data = service.workbench(db, order_id)
    if data is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    return data


class OrderMainEditBody(BaseModel):
    """订单主档编辑：字段传了才改；1688 订单改源单事实，工作流独有单改副本。"""

    supplier_name: str | None = None
    title: str | None = None
    ordered_at: date | None = None
    goods_total: str | None = None
    freight: str | None = None
    discount: str | None = None
    actual_payment: str | None = None
    order_amount: str | None = None
    paid_amount: str | None = None
    platform: str | None = None


@router.patch("/orders/{order_id}/main-fields")
def edit_order_main_fields(order_id: int, body: OrderMainEditBody, request: Request, db: Session = Depends(get_db)) -> dict:
    """网页交互编辑订单主档（金额/供应商/标题等）：源单与副本同步更新，留审计。"""
    try:
        return service.edit_order_main_fields(
            db, order_id=order_id, actor=current_actor(request), **body.model_dump()
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


class OrderKindOverrideBody(BaseModel):
    kind: str = Field(..., pattern="^(goods|consumable|)$")


@router.patch("/orders/{order_id}/kind-override")
def set_order_kind_override(order_id: int, body: OrderKindOverrideBody, request: Request, db: Session = Depends(get_db)) -> dict:
    """人工覆盖订单类型（正品/耗材）。

    自动判定依据（耗材档案「采购订货号」引用 + 供应商关键词）可能因 Excel
    数据填错而误判（例：5092031449423821020 生豆代烘焙单被外包装行引用）。
    人工覆盖优先于自动判定；传空串恢复自动判定。
    """
    actor = current_actor(request)
    external: ExternalPurchaseOrder | None = None
    for candidate_order, candidate_external in _source_pairs(db):
        candidate_id = candidate_order.id if candidate_order is not None else -candidate_external.id  # type: ignore[union-attr]
        if candidate_id == order_id:
            external = candidate_external
            break
    if external is None:
        raise HTTPException(status_code=404, detail="订单不存在或已被删除")
    old = external.order_kind_override or ""
    external.order_kind_override = body.kind
    db.commit()
    audit(db, actor, "purchase.order.kind_override", "external_purchase_orders", external.id,
          {"orderNo": external.external_order_id, "from": old, "to": body.kind})
    return {"orderId": order_id, "orderKindOverride": external.order_kind_override}


@router.post("/orders/{order_id}/soft-delete")
def soft_delete_order(order_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    """把订单从采购工作台移除（软删除，可恢复）。

    工作台 ID 约定：1688 导入订单用正数（alibaba1688_orders.id），
    工作流独有订单用负数（-external_purchase_orders.id）。

    - 1688 导入订单（order_id > 0）：只把 ``alibaba1688_orders.row_status`` 置为
      ``deleted``。链路 ``_source_pairs`` 会按订单号把同单号的工作流副本一并排除，
      因此整单退出工作台，但数据不丢——把 row_status 改回 ``active`` 即可恢复。
    - 工作流独有订单（order_id < 0，没有 1688 原件）：没有可软删的原件，
      只能删除工作流记录本体及其直接子项，此路径不可恢复，UI 需二次确认。
    """
    actor = current_actor(request)
    order: Alibaba1688Order | None = None
    external: ExternalPurchaseOrder | None = None
    for candidate_order, candidate_external in _source_pairs(db):
        candidate_id = candidate_order.id if candidate_order is not None else -candidate_external.id  # type: ignore[union-attr]
        if candidate_id == order_id:
            order, external = candidate_order, candidate_external
            break
    if order is None and external is None:
        raise HTTPException(status_code=404, detail="订单不存在或已被删除")

    order_no = order.external_order_id if order is not None else external.external_order_id  # type: ignore[union-attr]
    removed_po_ids: list[int] = []

    if order is not None:
        # 软删除 1688 原件：整单退出链路，工作流副本按订单号一并隐藏。
        order.row_status = "deleted"
        db.add(order)
        audit(db, actor, "procurement.workbench.order_soft_delete", "alibaba1688_orders", order.id,
              {"orderNo": order_no, "poId": external.id if external is not None else None})
    else:
        # 工作流独有订单：删除记录本体及其直接子项。
        removed_po_ids.append(external.id)  # type: ignore[union-attr]
        db.query(PurchaseAllocationItem).filter(PurchaseAllocationItem.po_id == external.id).delete(synchronize_session=False)  # type: ignore[union-attr]
        db.query(PurchaseExtraExpense).filter(PurchaseExtraExpense.po_id == external.id).delete(synchronize_session=False)  # type: ignore[union-attr]
        db.query(JackyunPurchaseOrderLink).filter(JackyunPurchaseOrderLink.po_id == external.id).delete(synchronize_session=False)  # type: ignore[union-attr]
        db.query(PurchaseInvoiceLink).filter(PurchaseInvoiceLink.po_id == external.id).delete(synchronize_session=False)  # type: ignore[union-attr]
        db.query(ProcurementChainLink).filter(ProcurementChainLink.external_po_id == external.id).delete(synchronize_session=False)  # type: ignore[union-attr]
        audit(db, actor, "procurement.workbench.order_delete", "external_purchase_orders", external.id,  # type: ignore[union-attr]
              {"orderNo": order_no})
        db.delete(external)
    db.commit()
    return {
        "ok": True,
        "orderId": order_id,
        "orderNo": order_no,
        "removedPoIds": removed_po_ids,
        "recoverable": order is not None,
    }


@router.get("/suppliers")
def supplier_list(
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """供应商聚合列表（管理视角）。"""
    return service.suppliers(db, limit=limit, offset=offset)


@router.get("/suppliers/{supplier_name}")
def supplier_workbench(supplier_name: str, db: Session = Depends(get_db)) -> dict:
    """单个供应商详情：历史合作 + 常购 SKU + 最近订单。"""
    data = service.supplier_workbench(db, supplier_name)
    if data is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    return data
