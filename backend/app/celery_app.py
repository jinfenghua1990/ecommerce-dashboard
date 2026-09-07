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
)


def _jackyun_schedules() -> dict:
    """吉客云 beat 表（按 JACKYUN_SYNC_MODE 三档切换）。

    - auto   = 原高频表 ~533 次/日（15/30/60 分钟节奏，正式 key 用）。
    - test   = 低频表 ~161 次/日：订单/售后类每小时、库存与采购类每 2 小时，留 ~45% 余量给重试与手动触发。
    - manual = 返回空表：关闭全部吉客云自动同步，仅工作台按钮 / POST /automation/run/jackyun/{job_type} 手动触发。
    每档错峰分钟，避免同一分钟并发打 MCP。
    """
    if settings.JACKYUN_SYNC_MODE == "manual":
        return {}
    if settings.JACKYUN_SYNC_MODE == "test":
        return {
            # 销售订单统一三通道：每小时（错峰）
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
            # 库存：每 2 小时 → 12
            "jackyun-inventory-2h": {
                "task": "tasks.sync_jackyun",
                "schedule": crontab(minute=43, hour="*/2"),
                "args": ("inventory",),
            },
            # 采购类：每 2 小时错峰 → 4 × 12 = 48
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
        # 吉客云销售订单统一三通道，默认每 30 分钟
        "jky-orders-interval": {
            "task": "tasks.sync_jky_orders",
            "schedule": crontab(minute=f"*/{interval}"),
        },
        # 吉客云：售后 15 分钟（错峰）
        "jackyun-aftersales-15min": {
            "task": "tasks.sync_jackyun",
            "schedule": crontab(minute="7,22,37,52"),
            "args": ("aftersales",),
        },
        # 吉客云：库存 30 分钟
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
        # 吉客云：采购 60 分钟
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


def _beat_schedule() -> dict:
    sched: dict = {}
    # manual 模式：吉客云全部自动同步关闭（含每日低频任务），仅保留非吉客云任务
    if settings.JACKYUN_SYNC_MODE != "manual":
        sched.update(_jackyun_schedules())
        sched.update({
            # 以下为每天/低频任务，各模式共用
            # 吉客云：商品/SKU 每天 03:10
            "jackyun-products-daily": {
            "task": "tasks.sync_jackyun",
            "schedule": crontab(hour=3, minute=10),
            "args": ("products",),
        },
        # 吉客云：SKU/价格主档（商品之后）
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
        })
    # 非吉客云任务始终保留
    sched.update({
        # 数据中心导入回收站：每天 04:15 清理超过保留期（默认 30 天）的软删除导入
        "recycle-bin-purge-daily": {
            "task": "tasks.recycle_bin_purge",
            "schedule": crontab(hour=4, minute=15),
        },
        # 吉客云 Web Adapter：每天 03:30 一次（销售/采购入库/库存全流程）
        "jky-web-daily": {
            "task": "tasks.sync_jky_web",
            "schedule": crontab(hour=3, minute=30),
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
        # 销售出库报表：每月 2 日 04:10 生成上月 CSV 并归档进财务资料中心（错峰 daily outbound 03:50）
        "sales-outbound-monthly": {
            "task": "tasks.generate_monthly_sales_outbound",
            "schedule": crontab(hour=4, minute=10, day_of_month=2),
        },
    })
    return sched


# beat_schedule 依赖 JACKYUN_TEST_MODE 分支，需在函数定义之后赋值
celery_app.conf.beat_schedule = _beat_schedule()
