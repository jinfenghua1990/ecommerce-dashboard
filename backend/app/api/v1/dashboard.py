from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import dashboard

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/sales-trend")
def sales_trend(days: int = 30, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return dashboard.sales_trend(db, days=min(max(days, 7), 365))


@router.get("/platform-ranking")
def platform_ranking(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return dashboard.platform_ranking(db)


@router.get("/sku-ranking")
def sku_ranking(limit: int = 20, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return dashboard.sku_ranking(db, limit=min(max(limit, 1), 100))


@router.get("/inventory")
def inventory(db: Session = Depends(get_db)) -> dict[str, Any]:
    return dashboard.inventory_summary(db)


@router.get("/orders")
def orders(status: str | None = None, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return dashboard.list_orders(db, status=status)


@router.get("/aftersales")
def aftersales(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return dashboard.list_aftersales(db)
