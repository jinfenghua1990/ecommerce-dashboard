from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.core.audit import audit
from app.config import settings
from app.db import get_db
from app.models.integration import SyncJob, SyncLog

router = APIRouter(prefix="/automation", tags=["automation"])

# 与 celery_app.beat_schedule 对应的说明表；订单频率跟随服务端配置。
_order_interval = max(1, min(settings.JKY_ORDER_SYNC_INTERVAL_MINUTES, 59))
_order_frequency = (
    "已暂停（JACKYUN_SYNC_MODE=manual）"
    if settings.JACKYUN_SYNC_MODE == "manual"
    else f"每 {_order_interval} 分钟"
)
SCHEDULE = [
    {"task": "tasks.sync_jky_orders", "args": "", "label": "吉客云 销售订单（三通道自动切换）", "frequency": _order_frequency},
    {"task": "tasks.sync_jackyun", "args": "aftersales", "label": "吉客云 售后", "frequency": "每 15 分钟（错峰）"},
    {"task": "tasks.sync_jackyun", "args": "inventory", "label": "吉客云 库存", "frequency": "每 30 分钟"},
    {"task": "tasks.sync_jackyun", "args": "products", "label": "吉客云 商品/SKU", "frequency": "每天 03:10"},
    {"task": "tasks.sync_jackyun", "args": "price_lists", "label": "吉客云 SKU/价格", "frequency": "每天 03:20"},
    {"task": "tasks.sync_jackyun", "args": "warehouses", "label": "吉客云 仓库", "frequency": "每天 03:30"},
    {"task": "tasks.sync_jackyun", "args": "purchase", "label": "吉客云 采购", "frequency": "每 60 分钟"},
    {"task": "tasks.sync_jackyun", "args": "online_orders", "label": "吉客云 OMS 订单", "frequency": "每 15 分钟（错峰）"},
    {"task": "tasks.sync_jackyun", "args": "shop_orders", "label": "吉客云 网店订单/发货", "frequency": "每 15 分钟（错峰）"},
    {"task": "tasks.sync_jackyun", "args": "purchase_settlements", "label": "吉客云 采购结算", "frequency": "每 60 分钟（错峰）"},
    {"task": "tasks.sync_jackyun", "args": "purchase_returns", "label": "吉客云 采购退货", "frequency": "每 60 分钟（错峰）"},
    {"task": "tasks.sync_jackyun", "args": "stock_allocations", "label": "吉客云 库存调拨", "frequency": "每 60 分钟（错峰）"},
    {"task": "tasks.sync_jackyun", "args": "inbound", "label": "吉客云 入库单", "frequency": "每天 03:40"},
    {"task": "tasks.sync_jackyun", "args": "outbound", "label": "吉客云 出库单", "frequency": "每天 03:50"},
    {"task": "tasks.sync_1688", "args": "", "label": "1688 订单", "frequency": "每天 07:30"},
    {"task": "tasks.monthly_verify", "args": "", "label": "月初完整校验", "frequency": "每月 1 日 06:00"},
]

JACKYUN_JOB_TYPES = {
    "sales", "online_orders", "aftersales", "inventory", "products", "price_lists",
    "warehouses", "purchase", "purchase_settlements", "purchase_returns", "inbound", "outbound",
    "stock_allocations", "shop_orders",
}


@router.post("/run/jackyun/{job_type}")
def run_jackyun(job_type: str, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    if job_type not in JACKYUN_JOB_TYPES:
        raise HTTPException(400, f"未知吉客云同步类型: {job_type}")
    if job_type == "sales":
        from app.tasks.sync import sync_jky_orders

        task = sync_jky_orders.delay()
        audit(db, current_actor(request), "automation.jky_order.queued", "celery_task", task.id)
        return {"ok": True, "taskId": task.id, "jobType": "orders", "status": "queued"}

    from app.tasks.sync import sync_jackyun

    # 人工“立即同步”可在客户经理开通权限后绕过自动暂停，得到一次真实验证结果。
    task = sync_jackyun.delay(job_type, True)
    audit(db, current_actor(request), "automation.jackyun.queued", "celery_task", task.id,
          {"jobType": job_type})
    return {"ok": True, "taskId": task.id, "jobType": job_type, "status": "queued"}


@router.post("/run/1688")
def run_1688(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.tasks.sync import sync_1688

    task = sync_1688.delay()
    audit(db, current_actor(request), "automation.1688.queued", "celery_task", task.id)
    return {"ok": True, "taskId": task.id, "status": "queued"}


@router.get("/schedule")
def schedule() -> dict[str, Any]:
    """只读展示 beat schedule 和当前订单同步调度档位。"""
    return {"items": SCHEDULE, "note": "所有外部同步仅在凭证配置后真正执行；未配置如实跳过。吉客云业务权限被明确拒绝后，定时任务会暂停，待开通后请手动立即同步验证。"}


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
