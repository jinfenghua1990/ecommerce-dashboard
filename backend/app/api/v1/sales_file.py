"""销售清单：吉客云《销售单查询》Excel 导入 + 全部销售明细台账查询。"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.db import get_db
from app.models.sales import SalesOrder, SalesOrderItem
from app.services import sales_file_import_service as svc

router = APIRouter(prefix="/sales-file", tags=["sales-file"])


@router.post("/import")
async def import_sales_file(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    """上传吉客云客户端导出的《销售单查询.xlsx》（销售单+销售单货品 双 sheet）。

    按 JY 订单号幂等 upsert 到 sales_orders / sales_order_items，
    取消/待审核单不导入；库内被本文件标记取消的残留单会删除。
    """
    try:
        content = await file.read()
        if len(content) > svc.MAX_FILE_BYTES:
            raise HTTPException(413, "文件超过 100 MiB 上限")
        return svc.import_sales_file(
            db,
            content=content,
            original_name=file.filename or "sales-list.xlsx",
            actor=current_actor(request),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/detail")
def sales_detail(
    q: str = Query("", description="订单号/网店单号/货品编号/货品名称 模糊"),
    platform: str = Query("", description="销售渠道精确筛选"),
    sku: str = Query("", description="货品编号精确筛选（总览 SKU 排行点击穿透用）"),
    status: str = Query("", description="订单状态精确筛选"),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    """全部销售明细：订单 × 货品行级台账。

    手工导入（jky_file）与吉客云 API 三通道（jky_web/jky_rpa/jky_api）写同一张
    sales_orders / sales_order_items，字段口径一致，这里不做来源区分、全量展示
    （含取消单，用订单状态列自行筛）。
    """
    cond = []
    if q.strip():
        like = f"%{q.strip()}%"
        cond.append(or_(
            SalesOrder.order_no.ilike(like),
            SalesOrder.order_no.in_(
                db.query(SalesOrder.order_no).filter(SalesOrder.raw["netOrderNo"].astext.ilike(like))
            ),
            SalesOrderItem.sku_code.ilike(like),
            SalesOrderItem.goods_name.ilike(like),
        ))
    if platform:
        cond.append(SalesOrder.platform == platform)
    if sku.strip():
        cond.append(SalesOrderItem.sku_code == sku.strip())
    if status:
        cond.append(SalesOrder.order_status == status)
    if start_date:
        cond.append(SalesOrder.ordered_at >= start_date)
    if end_date:
        end_next = date.fromordinal(end_date.toordinal() + 1)
        cond.append(SalesOrder.ordered_at < end_next)

    base = db.query(SalesOrderItem, SalesOrder).join(
        SalesOrder, SalesOrderItem.order_id == SalesOrder.id
    )
    if cond:
        base = base.filter(or_(*cond)) if len(cond) == 1 else base.filter(*cond)
    total = base.count()
    rows = (
        base.order_by(
            SalesOrder.ordered_at.desc().nullslast(),
            SalesOrder.order_no.desc(),
            SalesOrderItem.id,
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    def _fmt(dt) -> str | None:
        return dt.strftime("%Y-%m-%d %H:%M") if dt else None

    out_rows = []
    for item, order in rows:
        o_raw = order.raw or {}
        i_raw = item.raw or {}
        out_rows.append({
            "itemId": item.id,
            "orderId": order.id,
            "orderNo": order.order_no,
            "netOrderNo": o_raw.get("netOrderNo") or "",
            "orderedAt": _fmt(order.ordered_at),
            "paidAt": _fmt(order.paid_at),
            "platform": order.platform or "",
            "orderType": order.order_type or "",
            "orderStatus": order.order_status or "",
            "payStatus": order.pay_status or "",
            "settleStatus": o_raw.get("settleStatus") or "",
            "warehouse": o_raw.get("warehouse") or "",
            "logisticsNo": o_raw.get("logisticsNo") or "",
            "logisticsCompany": o_raw.get("logisticsCompany") or "",
            "buyerNote": order.buyer_note or "",
            "goodsCount": o_raw.get("goodsCount"),
            "goodsCost": o_raw.get("goodsCost"),
            "grossProfit": o_raw.get("grossProfit"),
            "skuCode": item.sku_code or "",
            "goodsName": item.goods_name or "",
            "spec": i_raw.get("spec") or "",
            "unitName": i_raw.get("unitName") or "",
            "gift": i_raw.get("gift") or "",
            "quantity": float(item.quantity) if item.quantity is not None else None,
            "unitPrice": float(item.unit_price) if item.unit_price is not None else None,
            "amount": float(item.amount) if item.amount is not None else None,
            "discountAmount": float(item.discount_amount) if item.discount_amount is not None else None,
            "orderAmount": float(order.order_amount) if order.order_amount is not None else None,
            "paidAmount": float(order.paid_amount) if order.paid_amount is not None else None,
        })

    platforms = [
        r[0] for r in db.query(SalesOrder.platform).filter(SalesOrder.platform != "").distinct().order_by(SalesOrder.platform).all()
    ]
    statuses = [
        r[0] for r in db.query(SalesOrder.order_status).filter(SalesOrder.order_status != "").distinct().order_by(SalesOrder.order_status).all()
    ]
    return {
        "rows": out_rows,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "platforms": platforms,
        "statuses": statuses,
    }
