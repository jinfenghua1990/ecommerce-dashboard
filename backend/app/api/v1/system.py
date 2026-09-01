from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import integration_service

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, Any]:
    components = {"database": "down", "redis": "down"}
    try:
        db.execute(text("SELECT 1"))
        components["database"] = "up"
    except Exception:
        pass
    try:
        import redis as redis_lib

        from app.config import settings as s

        r = redis_lib.Redis.from_url(s.REDIS_URL, socket_connect_timeout=2)
        components["redis"] = "up" if r.ping() else "down"
    except Exception:
        pass
    status = "ready" if all(v == "up" for v in components.values()) else "degraded"
    return {"status": status, "components": components}


@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.models.ops import ExceptionRecord

    pending_exceptions = (
        db.query(ExceptionRecord).filter(ExceptionRecord.status == "pending").count()
    )
    return {
        "phase": 0,
        "phaseName": "Phase 0 工程基础",
        "accessMode": "lan_trusted",
        "dataState": "empty",
        "integrations": integration_service.integration_status(db),
        "pendingExceptions": pending_exceptions,
        "nextMilestone": "Phase 1 吉客云真实连接与主数据",
        # 经营指标（Phase 2 数据落地后填充真实值；现在如实为空）
        "metrics": {
            "salesAmount": None, "netSales": None, "orderCount": None, "refundRate": None,
            "grossProfit": None, "receivable": None, "received": None, "pendingReceive": None,
        },
    }
