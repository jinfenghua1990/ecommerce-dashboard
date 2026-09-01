from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.integration import SyncJob, SyncLog

router = APIRouter(prefix="/automation", tags=["automation"])

# 与 celery_app.beat_schedule 对应的说明表（V1 固定频率，后台可配置列为下一步）
SCHEDULE = [
    {"task": "tasks.sync_jackyun", "args": "sales", "label": "吉客云 订单/售后", "frequency": "每 15 分钟"},
    {"task": "tasks.sync_jackyun", "args": "inventory", "label": "吉客云 库存", "frequency": "每 30 分钟"},
    {"task": "tasks.sync_jackyun", "args": "products", "label": "吉客云 商品/SKU", "frequency": "每天 03:10"},
    {"task": "tasks.sync_jackyun", "args": "purchase", "label": "吉客云 采购", "frequency": "每 60 分钟"},
    {"task": "tasks.sync_1688", "args": "", "label": "1688 订单", "frequency": "每天 07:30"},
    {"task": "tasks.monthly_verify", "args": "", "label": "月初完整校验", "frequency": "每月 1 日 06:00"},
]


@router.get("/schedule")
def schedule() -> dict[str, Any]:
    """只读展示 beat schedule（V1 频率固定，可配置化列入下一迭代）。"""
    return {"items": SCHEDULE, "note": "所有外部同步仅在凭证配置后真正执行；未配置如实跳过（见同步日志）"}


@router.get("/jobs")
def jobs(limit: int = 50, provider: str | None = None,
         db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    q = db.query(SyncJob).order_by(SyncJob.id.desc())
    if provider:
        q = q.filter(SyncJob.provider == provider)
    return [
        {
            "id": j.id, "provider": j.provider, "jobType": j.job_type,
            "status": j.status, "startedAt": j.started_at.isoformat() if j.started_at else None,
            "finishedAt": j.finished_at.isoformat() if j.finished_at else None,
            "stats": j.stats or {}, "errorSummary": j.error_summary or "",
        }
        for j in q.limit(min(max(limit, 1), 200)).all()
    ]


@router.get("/logs")
def logs(limit: int = 100, provider: str | None = None,
         db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    q = db.query(SyncLog).order_by(SyncLog.id.desc())
    if provider:
        q = q.filter(SyncLog.provider == provider)
    return [
        {
            "id": r.id, "provider": r.provider, "level": r.level,
            "message": r.message, "jobId": r.sync_job_id,
        }
        for r in q.limit(min(max(limit, 1), 300)).all()
    ]
