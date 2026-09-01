import json

from celery import Celery
from celery.schedules import crontab
from celery.signals import task_failure

from app.config import settings

"""Celery 编排（规格 13）。所有频率后台可配置，V1 先以默认值落 beat schedule。"""

celery_app = Celery(
    "ecommerce_ops",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.sync"],
)

# 死信队列：Redis 无原生 DLX，用 task_failure 信号把「最终失败」落 Redis list。
# 中间 retry 走 task_retry 信号、不会触发 task_failure，故此处天然只收「重试耗尽后的最终失败」。
DEAD_LETTER_KEY = "ecommerce:dead-letter"
DEAD_LETTER_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 天


def _push_dead_letter(payload: dict) -> None:
    import redis as redis_lib

    r = redis_lib.from_url(settings.REDIS_URL)
    r.lpush(DEAD_LETTER_KEY, json.dumps(payload, ensure_ascii=False))
    r.expire(DEAD_LETTER_KEY, DEAD_LETTER_TTL_SECONDS)


@task_failure.connect
def _on_task_failure(sender=None, task_id=None, exception=None, args=None, kwargs=None, **rest):
    # 仅在最终失败时记录；MaxRetriesExceededError 或一次性失败都属最终失败
    try:
        _push_dead_letter(
            {
                "task": getattr(sender, "name", None),
                "task_id": task_id,
                "exception": type(exception).__name__ if exception else None,
                "detail": str(exception)[:500] if exception else None,
                "args": list(args or []),
                "kwargs": dict(kwargs or {}),
            }
        )
    except Exception:  # 死信写入失败不得影响主流程
        pass


celery_app.conf.update(
    timezone=settings.TZ,
    enable_utc=False,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # 启动时 broker 未就绪自动重连（容器编排下 redis 可能晚于 worker 就绪）
    broker_connection_retry_on_startup=True,
    # worker 崩溃（OOM/SIGKILL）时消息拒绝重回队列，避免 ack 后静默丢失
    task_reject_on_worker_lost=True,
    # 任务超时护栏：MCP/网络调用可能 hang，软超时先触发可捕获异常，硬超时兜底 kill
    task_soft_time_limit=600,
    task_time_limit=900,
    # 任务结果 1 小时后过期，避免 Redis 内存被结果元数据堆积
    result_expires=3600,
    # 全局退避兜底：单任务未显式写 countdown 时按 2^n 退避，封顶 600s
    task_retry_backoff=True,
    task_retry_backoff_max=600,
    beat_schedule={
        # 吉客云：订单/售后 15 分钟
        "jackyun-sales-15min": {
            "task": "tasks.sync_jackyun",
            "schedule": crontab(minute="*/15"),
            "args": ("sales",),
        },
        # 吉客云：库存 30 分钟
        "jackyun-inventory-30min": {
            "task": "tasks.sync_jackyun",
            "schedule": crontab(minute="*/30"),
            "args": ("inventory",),
        },
        # 吉客云：商品/SKU 每天 03:10
        "jackyun-products-daily": {
            "task": "tasks.sync_jackyun",
            "schedule": crontab(hour=3, minute=10),
            "args": ("products",),
        },
        # 吉客云：采购 60 分钟
        "jackyun-purchase-hourly": {
            "task": "tasks.sync_jackyun",
            "schedule": crontab(minute=5),
            "args": ("purchase",),
        },
        # 1688：每天 07:30 一次
        "alibaba1688-daily": {
            "task": "tasks.sync_1688",
            "schedule": crontab(hour=7, minute=30),
        },
        # 月初对上月完整校验（每月 1 日 06:00）
        "monthly-verify": {
            "task": "tasks.monthly_verify",
            "schedule": crontab(hour=6, minute=0, day_of_month=1),
        },
    },
)
