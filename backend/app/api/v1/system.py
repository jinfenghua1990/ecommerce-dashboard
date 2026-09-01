from typing import Any

from fastapi import APIRouter, Depends
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
def overview(db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.config import settings
    from app.models.ops import ExceptionRecord
    from app.services import dashboard

    pending_exceptions = (
        db.query(ExceptionRecord).filter(ExceptionRecord.status == "pending").count()
    )
    return {
        "phase": 6,
        "phaseName": "Phase 6 期初+异常+月结",
        "accessMode": settings.ACCESS_MODE,
        "dataState": "partial",
        "integrations": integration_service.integration_status(db),
        "pendingExceptions": pending_exceptions,
        "nextMilestone": "吉客云开放平台开通 → Phase 1 数据落地 → 经营看板出数",
        # 经营指标：真实聚合本地库（规格 4 首屏 9 指标），数据为空如实 None
        "metrics": dashboard.overview_metrics(db),
    }
