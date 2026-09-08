from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db import get_db
from app.services import integration_service

router = APIRouter(prefix="/system", tags=["system"])
_log = get_logger("system.health")


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, Any]:
    components = {"database": "down", "redis": "down"}
    try:
        db.execute(text("SELECT 1"))
        components["database"] = "up"
    except Exception as exc:
        _log.warning("database health probe failed: %s", exc)
    try:
        import redis as redis_lib

        from app.config import settings as s

        r = redis_lib.Redis.from_url(s.REDIS_URL, socket_connect_timeout=2)
        components["redis"] = "up" if r.ping() else "down"
    except Exception as exc:
        _log.warning("redis health probe failed: %s", exc)
    status = "ready" if all(v == "up" for v in components.values()) else "degraded"
    return {"status": status, "components": components}


@router.get("/overview")
def overview(
    period_year: int | None = Query(None, ge=2000, le=2100),
    period_month: int | None = Query(None, ge=1, le=12),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """经营总览只展示一个自然月；缺省为当前业务时区月份，不再返回全历史累计。"""
    from app.config import settings
    from app.models.ops import ExceptionRecord
    from app.services import monthly_core, profit as profit_service

    now = datetime.now(ZoneInfo(settings.TZ))
    year = period_year or now.year
    month = period_month or now.month

    metrics = monthly_core.sales_overview(db, year, month)
    recon = monthly_core.reconciliation_overview(db, year, month)
    profit = profit_service.compute(db, year, month)
    metrics["grossProfit"] = profit.get("grossProfit")
    metrics["receivable"] = recon.get("receivable")
    metrics["received"] = recon.get("received")
    metrics["pendingReceive"] = recon.get("pending")

    pending_exceptions = (
        db.query(ExceptionRecord).filter(ExceptionRecord.status == "pending").count()
    )
    return {
        "phase": 6,
        "phaseName": "Phase 6 期初+异常+月结",
        "period": f"{year}-{month:02d}",
        "accessMode": settings.ACCESS_MODE,
        "dataState": "partial",
        "integrations": integration_service.integration_status(db),
        "pendingExceptions": pending_exceptions,
        "nextMilestone": "供应链月度经营闭环与月结口径统一",
        "metrics": metrics,
    }
