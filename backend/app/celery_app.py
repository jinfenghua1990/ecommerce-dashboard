import json

from celery import Celery
from celery.schedules import crontab
from celery.signals import task_failure

from app.config import settings

"""Celery 编排。月度经营模式默认不自动拉取吉客云全量数据；1688 日同步、采购入库轻量跟踪和月度任务独立保留。"""

celery_app = Celery(
    "ecommerce_ops",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.tasks.sync",
        "app.tasks.recycle_bin",
        "app.tasks.jky_procurement_tracking",
    ],
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
    broker_connection_retry_on_startup=True,
    task_reject_on_worker_lost=True,
    task_soft_time_limit=600,
    task_time_limit=900,
    result_expires=3600,
    task_retry_backoff=True,
    task_retry_backoff_max=600,
)


def _jackyun_schedules() -> dict:
    """吉客云 beat 表（按 JACKYUN_SYNC_MODE 三档切换）。

    manual 是本项目默认：吉客云全量/业务接口自动同步关闭，仅允许人工触发或月结前主动导入。
    注意：1688 采购链所需的“采购入库轻量跟踪”不走这里，单独低频执行。
    """
    if settings.JACKYUN_SYNC_MODE == "manual":
        return {}
    if settings.JACKYUN_SYNC_MODE == "test":
        return {
            "jky-orders-hourly": {
                "task": "tasks.sync_jky_orders",
                "schedule": crontab(minute=3),
            },
            "jackyun-aftersales-hourly": {
                "task": "tasks.sync_jackyun",
                "schedule": crontab(minute=13),
                "args": ("aftersales",),
            },
            "jackyun-online-orders-hourly": {
                "task": "tasks.sync_jackyun",
                "schedule": crontab(minute=23),
                "args": ("online_orders",),
            },
            "jackyun-shop-orders-hourly": {
                "task": "tasks.sync_jackyun",
                "schedule": crontab(minute=33),
                "args": ("shop_orders",),
            },
            "jackyun-inventory-2h": {
                "task": "tasks.sync_jackyun",
                "schedule": crontab(minute=43, hour="*/2"),
                "args": ("inventory",),
            },
            "jackyun-purchase-2h": {
                "task": "tasks.sync_jackyun",
                "schedule": crontab(minute=5, hour="1-23/2"),
                "args": ("purchase",),
            },
            "jackyun-purchase-settlements-2h": {
                "task": "tasks.sync_jackyun",
                "schedule": crontab(minute=15, hour="1-23/2"),
                "args": ("purchase_settlements",),
            },
            "jackyun-purchase-returns-2h": {
                "task": "tasks.sync_jackyun",
                "schedule": crontab(minute=25, hour="1-23/2"),
                "args": ("purchase_returns",),
            },
            "jackyun-stock-allocations-2h": {
                "task": "tasks.sync_jackyun",
                "schedule": crontab(minute=35, hour="1-23/2"),
                "args": ("stock_allocations",),
            },
        }
    interval = max(1, min(settings.JKY_ORDER_SYNC_INTERVAL_MINUTES, 59))
    return {
        "jky-orders-interval": {
            "task": "tasks.sync_jky_orders",
            "schedule": crontab(minute=f"*/{interval}"),
        },
        "jackyun-aftersales-15min": {
            "task": "tasks.sync_jackyun",
            "schedule": crontab(minute="7,22,37,52"),
            "args": ("aftersales",),
        },
        "jackyun-inventory-30min": {
            "task": "tasks.sync_jackyun",
            "schedule": crontab(minute="*/30"),
            "args": ("inventory",),
        },
        "jackyun-online-orders-15min": {
            "task": "tasks.sync_jackyun",
            "schedule": crontab(minute="2,17,32,47"),
            "args": ("online_orders",),
        },
        "jackyun-shop-orders-15min": {
            "task": "tasks.sync_jackyun",
            "schedule": crontab(minute="4,19,34,49"),
            "args": ("shop_orders",),
        },
        "jackyun-purchase-hourly": {
            "task": "tasks.sync_jackyun",
            "schedule": crontab(minute=5),
            "args": ("purchase",),
        },
        "jackyun-purchase-settlements-hourly": {
            "task": "tasks.sync_jackyun",
            "schedule": crontab(minute=15),
            "args": ("purchase_settlements",),
        },
        "jackyun-purchase-returns-hourly": {
            "task": "tasks.sync_jackyun",
            "schedule": crontab(minute=25),
            "args": ("purchase_returns",),
        },
        "jackyun-stock-allocations-hourly": {
            "task": "tasks.sync_jackyun",
            "schedule": crontab(minute=35),
            "args": ("stock_allocations",),
        },
    }


def _jackyun_daily_schedules() -> dict:
    """仅在 test/auto 模式启用的吉客云低频全量任务。manual 必须完全为空。"""
    if settings.JACKYUN_SYNC_MODE == "manual":
        return {}
    return {
        "jackyun-products-daily": {
            "task": "tasks.sync_jackyun",
            "schedule": crontab(hour=3, minute=10),
            "args": ("products",),
        },
        "jackyun-price-lists-daily": {
            "task": "tasks.sync_jackyun",
            "schedule": crontab(hour=3, minute=20),
            "args": ("price_lists",),
        },
        "jackyun-warehouses-daily": {
            "task": "tasks.sync_jackyun",
            "schedule": crontab(hour=3, minute=30),
            "args": ("warehouses",),
        },
        "jackyun-inbound-daily": {
            "task": "tasks.sync_jackyun",
            "schedule": crontab(hour=3, minute=40),
            "args": ("inbound",),
        },
        "jackyun-outbound-daily": {
            "task": "tasks.sync_jackyun",
            "schedule": crontab(hour=3, minute=50),
            "args": ("outbound",),
        },
        "jky-web-daily": {
            "task": "tasks.sync_jky_web",
            "schedule": crontab(hour=3, minute=30),
        },
    }


def _beat_schedule() -> dict:
    sched: dict = {}
    sched.update(_jackyun_schedules())
    sched.update(_jackyun_daily_schedules())

    # 与吉客云全量/正式 API 同步无关的轻量采购链任务仍保留。
    sched.update({
        "recycle-bin-purge-daily": {
            "task": "tasks.recycle_bin_purge",
            "schedule": crontab(hour=4, minute=15),
        },
        # 1688 按既定采购习惯每天同步一次。
        "alibaba1688-daily": {
            "task": "tasks.sync_1688",
            "schedule": crontab(hour=7, minute=30),
        },
        # 采购入库跟踪只抓吉客云入库主/明细，不抓销售和全量库存。
        # 白天每 2 小时核对一次，确保 1688 备注写入后能较快自动建链并跟踪收货。
        "jky-procurement-inbound-tracking-2h": {
            "task": "tasks.sync_jky_procurement_tracking",
            "schedule": crontab(minute=20, hour="8-22/2"),
        },
        "monthly-verify": {
            "task": "tasks.monthly_verify",
            "schedule": crontab(hour=6, minute=0, day_of_month=1),
        },
        "sales-outbound-monthly": {
            "task": "tasks.generate_monthly_sales_outbound",
            "schedule": crontab(hour=4, minute=10, day_of_month=2),
        },
    })
    return sched


celery_app.conf.beat_schedule = _beat_schedule()
