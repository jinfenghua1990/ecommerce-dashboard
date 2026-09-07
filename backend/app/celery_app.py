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
    include=["app.tasks.sync", "app.tasks.recycle_bin"],
)

DEAD_LETTER_KEY = "ecommerce:dead-letter"
DEAD_LETTER_TTL_SECONDS = 60 * 60 * 24 * 30


def _push_dead_letter(payload: dict) -> None:
    import redis as redis_lib

    r = redis_lib.from_url(settings.REDIS_URL)
    r.lpush(DEAD_LETTER_KEY, json.dumps(payload, ensure_ascii=False))
    r.expire(DEAD_LETTER_KEY, DEAD_LETTER_TTL_SECONDS)


@task_failure.connect
def _on_task_failure(sender=None, task_id=None, exception=None, args=None, kwargs=None, **rest):
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
    except Exception:
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
    """吉客云 beat 表（按 JACKYUN_SYNC_MODE 三档切换）。"""
    if settings.JACKYUN_SYNC_MODE == "manual":
        return {}
    if settings.JACKYUN_SYNC_MODE == "test":
        return {
            "jky-orders-hourly": {"task": "tasks.sync_jky_orders", "schedule": crontab(minute=3)},
            "jackyun-aftersales-hourly": {
                "task": "tasks.sync_jackyun", "schedule": crontab(minute=13), "args": ("aftersales",),
            },
            "jackyun-online-orders-hourly": {
                "task": "tasks.sync_jackyun", "schedule": crontab(minute=23), "args": ("online_orders",),
            },
            "jackyun-shop-orders-hourly": {
                "task": "tasks.sync_jackyun", "schedule": crontab(minute=33), "args": ("shop_orders",),
            },
            "jackyun-inventory-2h": {
                "task": "tasks.sync_jackyun", "schedule": crontab(minute=43, hour="*/2"), "args": ("inventory",),
            },
            "jackyun-purchase-2h": {
                "task": "tasks.sync_jackyun", "schedule": crontab(minute=5, hour="1-23/2"), "args": ("purchase",),
            },
            "jackyun-purchase-settlements-2h": {
                "task": "tasks.sync_jackyun", "schedule": crontab(minute=15, hour="1-23/2"),
                "args": ("purchase_settlements",),
            },
            "jackyun-purchase-returns-2h": {
                "task": "tasks.sync_jackyun", "schedule": crontab(minute=25, hour="1-23/2"),
                "args": ("purchase_returns",),
            },
            "jackyun-stock-allocations-2h": {
                "task": "tasks.sync_jackyun", "schedule": crontab(minute=35, hour="1-23/2"),
                "args": ("stock_allocations",),
            },
        }
    interval = max(1, min(settings.JKY_ORDER_SYNC_INTERVAL_MINUTES, 59))
    return {
        "jky-orders-interval": {"task": "tasks.sync_jky_orders", "schedule": crontab(minute=f"*/{interval}")},
        "jackyun-aftersales-15min": {
            "task": "tasks.sync_jackyun", "schedule": crontab(minute="7,22,37,52"), "args": ("aftersales",),
        },
        "jackyun-inventory-30min": {
            "task": "tasks.sync_jackyun", "schedule": crontab(minute="*/30"), "args": ("inventory",),
        },
        "jackyun-online-orders-15min": {
            "task": "tasks.sync_jackyun", "schedule": crontab(minute="2,17,32,47"), "args": ("online_orders",),
        },
        "jackyun-shop-orders-15min": {
            "task": "tasks.sync_jackyun", "schedule": crontab(minute="4,19,34,49"), "args": ("shop_orders",),
        },
        "jackyun-purchase-hourly": {
            "task": "tasks.sync_jackyun", "schedule": crontab(minute=5), "args": ("purchase",),
        },
        "jackyun-purchase-settlements-hourly": {
            "task": "tasks.sync_jackyun", "schedule": crontab(minute=15), "args": ("purchase_settlements",),
        },
        "jackyun-purchase-returns-hourly": {
            "task": "tasks.sync_jackyun", "schedule": crontab(minute=25), "args": ("purchase_returns",),
        },
        "jackyun-stock-allocations-hourly": {
            "task": "tasks.sync_jackyun", "schedule": crontab(minute=35), "args": ("stock_allocations",),
        },
    }


def _beat_schedule() -> dict:
    sched: dict = {}
    if settings.JACKYUN_SYNC_MODE != "manual":
        sched.update(_jackyun_schedules())
        sched.update({
            "jackyun-products-daily": {
                "task": "tasks.sync_jackyun", "schedule": crontab(hour=3, minute=10), "args": ("products",),
            },
            "jackyun-price-lists-daily": {
                "task": "tasks.sync_jackyun", "schedule": crontab(hour=3, minute=20), "args": ("price_lists",),
            },
            "jackyun-warehouses-daily": {
                "task": "tasks.sync_jackyun", "schedule": crontab(hour=3, minute=30), "args": ("warehouses",),
            },
            "jackyun-inbound-daily": {
                "task": "tasks.sync_jackyun", "schedule": crontab(hour=3, minute=40), "args": ("inbound",),
            },
            "jackyun-outbound-daily": {
                "task": "tasks.sync_jackyun", "schedule": crontab(hour=3, minute=50), "args": ("outbound",),
            },
        })
    sched.update({
        "recycle-bin-purge-daily": {
            "task": "tasks.recycle_bin_purge", "schedule": crontab(hour=4, minute=15),
        },
        "jky-web-daily": {"task": "tasks.sync_jky_web", "schedule": crontab(hour=3, minute=30)},
        "alibaba1688-daily": {"task": "tasks.sync_1688", "schedule": crontab(hour=7, minute=30)},
        "monthly-verify": {"task": "tasks.monthly_verify", "schedule": crontab(hour=6, minute=0, day_of_month=1)},
        # 财务默认交付：按官方开票“财务大类 + 税率”汇总；底层商品/SKU明细只在系统内保留。
        "accounting-summary-monthly": {
            "task": "tasks.generate_monthly_accounting_summary",
            "schedule": crontab(hour=4, minute=10, day_of_month=2),
        },
    })
    return sched


celery_app.conf.beat_schedule = _beat_schedule()
