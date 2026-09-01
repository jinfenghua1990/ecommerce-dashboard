from __future__ import annotations

from typing import Any

from app.celery_app import celery_app
from app.db import SessionLocal
from app.adapters.jackyun import JackyunAdapter, finish_sync_job, start_sync_job
from app.adapters.base import AdapterNotConfigured
from app.services.integration_service import ensure_exception


def _record(provider: str, job_type: str, status: str, message: str, stats: dict | None = None) -> None:
    db = SessionLocal()
    try:
        job = start_sync_job(db, provider, job_type)
        finish_sync_job(db, job, status, stats or {}, message if status != "success" else "")
        from app.models.integration import SyncLog

        db.add(SyncLog(provider=provider, level="info" if status == "success" else "warn",
                       message=message, sync_job_id=job.id))
        db.commit()
    finally:
        db.close()


@celery_app.task(name="tasks.sync_jackyun", bind=True, max_retries=3, default_retry_delay=60)
def sync_jackyun(self, job_type: str) -> dict[str, Any]:
    """吉客云定时同步。未配置→跳过；mapping 未实现→如实记录，不造假。"""
    db = SessionLocal()
    try:
        adapter = JackyunAdapter(db)
        try:
            adapter.ensure_configured()
        except AdapterNotConfigured as exc:
            _record("jackyun", job_type, "skipped", f"未配置，跳过: {exc}")
            return {"status": "skipped"}

        method_map = {
            "sales": "sync_sales_orders",
            "online_orders": "sync_online_orders",
            "aftersales": "sync_aftersales",
            "inventory": "sync_inventory",
            "products": "sync_products",
            "price_lists": "sync_price_lists",
            "warehouses": "sync_warehouses",
            "purchase": "sync_purchase_orders",
            "purchase_settlements": "sync_purchase_settlements",
            "purchase_returns": "sync_purchase_returns",
            "inbound": "sync_inbound",
            "outbound": "sync_outbound",
        }
        fn = method_map.get(job_type)
        if not fn:
            _record("jackyun", job_type, "failed", f"未知任务类型 {job_type}")
            return {"status": "unknown"}
        try:
            job = start_sync_job(db, "jackyun", job_type)
            stats = getattr(adapter, fn)()
            finish_sync_job(db, job, "success", stats or {})
            return {"status": "success", "stats": stats}
        except NotImplementedError:
            finish_sync_job(db, job, "skipped", {}, "mapping 待 Phase 1 真实字段样本")
            _record("jackyun", job_type, "skipped", "mapping 待 Phase 1 真实字段样本")
            return {"status": "pending_mapping"}
        except Exception as exc:  # 指数退避重试
            _record("jackyun", job_type, "failed", str(exc)[:500])
            ensure_exception(db, "JACKYUN_SYNC_FAIL", "吉客云同步失败", str(exc))
            raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
    finally:
        db.close()


@celery_app.task(name="tasks.sync_1688")
def sync_1688() -> dict[str, Any]:
    db = SessionLocal()
    try:
        from app.config import settings as s

        if not s.alibaba_1688_configured:
            _record("alibaba_1688", "orders", "skipped", "等待 1688 开放平台配置")
            return {"status": "skipped"}
        _record("alibaba_1688", "orders", "failed", "OAuth 交换与订单拉取待真实凭证后实现")
        return {"status": "pending_credentials"}
    finally:
        db.close()


@celery_app.task(name="tasks.monthly_verify")
def monthly_verify() -> dict[str, Any]:
    """月初对上月完整校验（规格 13）：财务资料完整性检查 + 缺失进异常中心。"""
    from datetime import date

    from app.services import finance_service
    from app.services.integration_service import ensure_exception

    today = date.today()
    # 上个月
    y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    db = SessionLocal()
    try:
        period = finance_service.refresh_period_status(
            db, finance_service.DEFAULT_COMPANY, y, m
        )
        missing = (period.missing_summary or {}).get("missing", {})
        if period.status != "SENT" and missing:
            ensure_exception(
                db, "FINANCE_INCOMPLETE", f"财务资料缺失 {y}-{m:02d}",
                f"缺少: {missing}（月度完整性检查，月初自动）",
            )
            _record("system", "monthly_verify", "warn",
                    f"{y}-{m:02d} 财务资料不完整: {missing}")
            return {"status": "incomplete", "period": f"{y}-{m:02d}", "missing": missing}
        _record("system", "monthly_verify", "success", f"{y}-{m:02d} 资料完整")
        return {"status": "complete", "period": f"{y}-{m:02d}"}
    except Exception as exc:  # 指数退避重试
        _record("system", "monthly_verify", "failed", str(exc)[:500])
        ensure_exception(db, "MONTHLY_VERIFY_FAIL", "月度校验失败", str(exc))
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
    finally:
        db.close()
