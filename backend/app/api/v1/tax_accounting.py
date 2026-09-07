"""税务做账：以官方发票为真值的月度底稿与差异检查。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import tax_accounting_service as service

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
