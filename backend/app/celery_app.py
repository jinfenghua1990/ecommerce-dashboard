from celery import Celery
from celery.schedules import crontab

from app.config import settings

"""Celery 编排（规格 13）。所有频率后台可配置，V1 先以默认值落 beat schedule。"""

celery_app = Celery(
    "ecommerce_ops",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.sync"],
)

celery_app.conf.update(
    timezone=settings.TZ,
    enable_utc=False,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
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
