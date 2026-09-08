"""1688 浏览器直采通道：登录态查询、扫码登录与立即同步 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.core.audit import audit
from app.db import get_db
from app.services.alibaba1688_browser_sync_service import browser_sync_status

router = APIRouter(prefix="/alibaba1688-browser", tags=["1688浏览器直采"])


@router.get("/status")
def get_browser_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    """浏览器通道状态（只读本地库，不打开浏览器）。"""
    return browser_sync_status(db)


@router.post("/login")
def start_login(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """弹出扫码登录任务：worker 所在机器（Mac mini）会打开 Chrome 窗口等待扫码。"""
    from app.tasks.sync import login_1688

    task = login_1688.delay()
    audit(db, current_actor(request), "alibaba1688.browser.login_queued", "celery_task", task.id)
    return {
        "ok": True,
        "taskId": task.id,
        "status": "queued",
        "message": "已在服务器发起扫码登录，请到 Mac mini 屏幕前完成扫码（超时 5 分钟）",
    }


@router.post("/sync")
def start_sync(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """立即同步：与自动化页「立即同步 1688」共用 tasks.sync_1688。"""
    from app.tasks.sync import sync_1688

    task = sync_1688.delay()
    audit(db, current_actor(request), "alibaba1688.browser.sync_queued", "celery_task", task.id)
    return {"ok": True, "taskId": task.id, "status": "queued"}


@router.get("/jobs")
def recent_jobs(limit: int = 10, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """最近的 1688 同步/登录任务结果（轮询用）。"""
    from app.models.integration import SyncJob

    limit = min(max(limit, 1), 50)
    rows = (
        db.query(SyncJob)
        .filter(SyncJob.provider == "alibaba_1688")
        .order_by(SyncJob.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": row.id,
            "jobType": row.job_type,
            "status": row.status,
            "startedAt": row.started_at.isoformat() if row.started_at else None,
            "finishedAt": row.finished_at.isoformat() if row.finished_at else None,
            "stats": row.stats or {},
            "errorSummary": row.error_summary or "",
        }
        for row in rows
    ]
