"""税务做账：以官方发票为真值的月度底稿与财务大类汇总。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import tax_accounting_service as service
from app.services import tax_finance_summary_service as finance_summary_service

router = APIRouter(prefix="/tax-accounting", tags=["tax-accounting"])


@router.get("/monthly-ledger")
def monthly_ledger(
    year: int = Query(..., ge=2000, le=9999),
    month: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
) -> dict:
    """返回月度税务做账真值底稿。

    规则：金额/税额以 active 的官方税务发票为准；数量/单价必须来自官方发票明细。
    吉客云、1688、手工数据只参与差异核对，不能覆盖税务真值。
    """
    try:
        return service.monthly_ledger(db, year, month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/finance-summary")
def finance_summary(
    year: int = Query(..., ge=2000, le=9999),
    month: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
) -> dict:
    """财务交付视图：按“财务大类 + 税率”汇总，底层明细永久保留。"""
    try:
        return finance_summary_service.build_finance_summary(db, year, month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/finance-summary.csv")
def finance_summary_csv(
    year: int = Query(..., ge=2000, le=9999),
    month: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
) -> Response:
    """下载实际发送给财务的主表；不包含逐条商品/SKU明细。"""
    try:
        report = finance_summary_service.build_finance_summary(db, year, month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not report["readyForFinanceDelivery"]:
        raise HTTPException(status_code=409, detail={
            "message": "财务大类汇总仍有阻塞项，禁止生成发送文件",
            "blockers": report["blockers"],
        })
    content = finance_summary_service.to_finance_csv(report)
    headers = {
        "Content-Disposition": f'attachment; filename="finance_sales_summary_{year}{month:02d}.csv"'
    }
    return Response(content=content, media_type="text/csv; charset=utf-8", headers=headers)
