"""1688 浏览器直采编排服务：Redis 互斥锁 → 登录检查 → 逐页捕获 → 增量 upsert → 虚拟批次。

架构定位（1688 Browser Sync V1）：
- 浏览器直采为主通道：Playwright 驱动真实 Chrome 打开「已买到的货品」，
  截获页面自身的 mtop 响应，解析订单后走与 Excel 导入完全相同的落库链路
  （upsert_order_data → Alibaba1688Order + ExternalPurchaseOrder）。
- 同步与登录共用一个持久化 Profile，同一时刻只允许一个浏览器实例
  （Redis SETNX 锁保证，跨 worker/beat/手动触发互斥）。
- 增量策略：订单列表按时间倒序，遇到连续 N 条已知订单即停止翻页，
  每天通常只访问 1~3 页，比全量翻页稳定得多。

本服务不做浏览器细节（见 adapters/alibaba1688_browser.py）与字段映射
（见 services/alibaba1688_mtop_mapper.py），只负责编排、落库与状态记录。
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.alibaba1688_browser import (
    Alibaba1688BrowserAdapter,
    BrowserCaptureError,
    BrowserLoginExpiredError,
    BrowserRiskControlError,
)
from app.adapters.jackyun import finish_sync_job, start_sync_job
from app.config import settings
from app.core.audit import audit
from app.models.alibaba1688_import import Alibaba1688FileImport, Alibaba1688Order
from app.models.integration import IntegrationConnection, SyncLog
from app.services.alibaba1688_import_service import upsert_order_data
from app.services.alibaba1688_mtop_mapper import extract_order_id, extract_orders, map_order
from app.services.integration_service import ensure_exception, get_or_create_connection

PROVIDER = "alibaba_1688"
CONNECTION_MODE = "浏览器直采"
# 同一持久化 Profile 同时只允许一个浏览器实例：同步任务与扫码登录都抢这把锁。
PROFILE_LOCK_KEY = "ecommerce:lock:alibaba1688-browser-profile"
PROFILE_LOCK_TTL_SECONDS = 15 * 60
VIRTUAL_BATCH_PATH_MARKER = "browser://direct-capture"


class SyncAlreadyRunningError(Exception):
    """同一 Profile 的浏览器任务已在执行（同步或登录）。"""


class _ProfileLock:
    """Redis SETNX 互斥锁；释放时校验 token 防止误删他人持有的锁。"""

    def __init__(self, key: str = PROFILE_LOCK_KEY, ttl_s: int = PROFILE_LOCK_TTL_SECONDS) -> None:
        self.key = key
        self.ttl_s = ttl_s
        self.token = secrets.token_hex(8)

    def __enter__(self) -> "_ProfileLock":
        import redis as redis_lib

        client = redis_lib.from_url(settings.REDIS_URL)
        ok = client.set(self.key, self.token, nx=True, ex=self.ttl_s)
        client.close()
        if not ok:
            raise SyncAlreadyRunningError("1688 浏览器任务已在执行中（同步或扫码登录占用 Profile）")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        import redis as redis_lib

        client = redis_lib.from_url(settings.REDIS_URL)
        try:
            # 仍持有同一 token 才删除，避免超时后误删他人新锁。
            if client.get(self.key) == self.token.encode():
                client.delete(self.key)
        finally:
            client.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connection(db: Session) -> IntegrationConnection:
    return get_or_create_connection(db, PROVIDER, CONNECTION_MODE, 4)


def _set_connection(
    db: Session,
    status: str,
    message: str = "",
    **meta_updates: Any,
) -> IntegrationConnection:
    conn = _connection(db)
    conn.status = status
    conn.error_summary = (message or "")[:500]
    meta = dict(conn.meta or {})
    meta.update(meta_updates)
    conn.meta = meta
    if status == "connected":
        conn.last_success_at = datetime.now(timezone.utc)
    conn.last_tested_at = datetime.now(timezone.utc)
    db.commit()
    return conn


def browser_sync_status(db: Session) -> dict[str, Any]:
    """只读本地库的浏览器通道状态（不打开浏览器，供前端轮询）。"""
    conn = (
        db.query(IntegrationConnection)
        .filter_by(provider=PROVIDER, mode=CONNECTION_MODE)
        .first()
    )
    last_sync_log = (
        db.query(SyncLog)
        .filter(SyncLog.provider == PROVIDER)
        .order_by(SyncLog.id.desc())
        .first()
    )
    meta = dict(conn.meta or {}) if conn else {}
    return {
        "enabled": settings.ALIBABA_1688_BROWSER_ENABLED,
        "profileDir": settings.alibaba_1688_browser_profile_dir,
        "headless": settings.ALIBABA_1688_BROWSER_HEADLESS,
        "status": conn.status if conn else "unconfigured",
        "account": meta.get("account"),
        "lastSyncAt": meta.get("lastSyncAt"),
        "lastSyncSummary": meta.get("lastSyncSummary"),
        "lastLoginCheckAt": meta.get("lastLoginCheckAt"),
        "errorSummary": (conn.error_summary or None) if conn else None,
        "lastSyncJobId": last_sync_log.sync_job_id if last_sync_log else None,
        "maxPages": settings.ALIBABA_1688_BROWSER_MAX_PAGES,
        "stopAfterKnown": settings.ALIBABA_1688_BROWSER_STOP_AFTER_KNOWN,
        "lookbackDays": settings.ALIBABA_1688_BROWSER_LOOKBACK_DAYS,
    }


def _dump_capture_samples(adapter: Alibaba1688BrowserAdapter) -> Path:
    """首捕调试：把原始 mtop 响应落盘，供回填字段映射路径。"""
    root = Path(settings.DATA_DIR).resolve() / "alibaba1688-mtop-samples"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = root / f"{stamp}-{secrets.token_hex(4)}.json"
    target.write_text(
        json.dumps(adapter.raw_responses, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    target.chmod(0o600)
    return target


def _create_virtual_batch(db: Session, actor: str, row_count: int) -> Alibaba1688FileImport:
    """浏览器同步批次：无原件文件，仅作为订单归属与生命周期载体。"""
    now = datetime.now(timezone.utc)
    token = secrets.token_hex(16)
    return Alibaba1688FileImport(
        original_name=f"浏览器直采 {now.strftime('%Y-%m-%d %H:%M')}",
        stored_path=VIRTUAL_BATCH_PATH_MARKER,
        sha256=hashlib.sha256(f"browser-sync:{token}".encode()).hexdigest(),
        size=0,
        mime="application/json",
        status="completed",
        sheet_name="mtop.1688.trading.dataline.service",
        headers=[],
        row_count=row_count,
        imported_order_count=0,
        error_summary="",
        uploader=actor,
        lifecycle="active",
        lifecycle_changed_at=now,
        parsed_at=now,
    )


def sync_orders(db: Session, *, actor: str = "system") -> dict[str, Any]:
    """主同步入口：打开浏览器 → 逐页捕获 mtop → 增量 upsert → 虚拟批次落库。"""
    if not settings.ALIBABA_1688_BROWSER_ENABLED:
        return {"status": "skipped", "reason": "browser channel disabled"}

    job = start_sync_job(db, PROVIDER, "browser_orders")
    try:
        with _ProfileLock():
            result = _sync_with_browser(db, actor=actor)
        job_status = "success" if result.get("status") == "success" else result.get("status", "failed")
        finish_sync_job(db, job, job_status, result.get("stats") or {}, result.get("message", ""))
        if result.get("status") == "success":
            # 同步恢复：自动关闭历史 1688 采集/配置故障告警（ensure_exception 的对称操作）
            from app.services.integration_service import resolve_exception

            resolve_exception(db, "ALIBABA_1688_BROWSER_CAPTURE_FAIL",
                              f"浏览器直采恢复（job #{job.id}），自动关闭")
            resolve_exception(db, "ALIBABA1688_NOT_CONFIGURED", "浏览器直采成功，配置有效，自动关闭")
        db.add(SyncLog(
            provider=PROVIDER,
            level="info" if result.get("status") == "success" else "warn",
            sync_job_id=job.id,
            message=result.get("message", ""),
            data={"stats": result.get("stats") or {}},
        ))
        db.commit()
        return result
    except SyncAlreadyRunningError as exc:
        message = str(exc)
        finish_sync_job(db, job, "skipped", {}, message)
        db.add(SyncLog(provider=PROVIDER, level="warn", sync_job_id=job.id, message=message))
        db.commit()
        return {"status": "already_running", "message": message}
    except BrowserLoginExpiredError as exc:
        message = str(exc)
        _set_connection(db, "needs_login", message, lastLoginCheckAt=_now_iso())
        finish_sync_job(db, job, "needs_login", {}, message)
        db.add(SyncLog(provider=PROVIDER, level="warn", sync_job_id=job.id, message=message))
        db.commit()
        return {"status": "login_required", "message": message}
    except BrowserRiskControlError as exc:
        message = str(exc)
        _set_connection(db, "error", message)
        finish_sync_job(db, job, "failed", {}, message)
        db.add(SyncLog(provider=PROVIDER, level="error", sync_job_id=job.id, message=message))
        ensure_exception(db, "ALIBABA_1688_BROWSER_RISK", "1688 浏览器直采触发风控", message)
        db.commit()
        return {"status": "risk_control", "message": message}
    except BrowserCaptureError as exc:
        message = str(exc)
        _set_connection(db, "error", message)
        finish_sync_job(db, job, "failed", {}, message)
        db.add(SyncLog(provider=PROVIDER, level="error", sync_job_id=job.id, message=message))
        ensure_exception(db, "ALIBABA_1688_BROWSER_CAPTURE_FAIL", "1688 浏览器直采捕获失败", message)
        db.commit()
        return {"status": "capture_failed", "message": message}
    except Exception as exc:  # noqa: BLE001 - 记录后由 Celery 重试
        message = str(exc)[:500]
        try:
            _set_connection(db, "error", message)
            finish_sync_job(db, job, "failed", {}, message)
            db.add(SyncLog(provider=PROVIDER, level="error", sync_job_id=job.id, message=message))
            db.commit()
        except Exception:  # noqa: BLE001 - 状态记录失败不掩盖原始异常
            db.rollback()
        raise


def _sync_with_browser(db: Session, *, actor: str) -> dict[str, Any]:
    """持锁执行浏览器捕获与落库（锁由调用方管理）。"""
    with Alibaba1688BrowserAdapter() as adapter:
        logged_in, account = adapter.check_login()
        _set_connection(
            db,
            "connected" if logged_in else "needs_login",
            "",
            lastLoginCheckAt=_now_iso(),
            **({"account": account} if account else {}),
        )
        if logged_in:
            # 会话有效即刷新登录态备份，保持 state JSON 与最新会话同步。
            adapter.export_state()
        if not logged_in:
            return {
                "status": "login_required",
                "message": "1688 登录态失效，请发起扫码登录",
            }

        if settings.ALIBABA_1688_BROWSER_CAPTURE_ONLY:
            # 首捕调试：只采样落盘，不写订单表。
            for _ in adapter.iter_order_pages():
                pass
            sample_path = _dump_capture_samples(adapter)
            return {
                "status": "capture_only",
                "message": f"首捕调试完成，原始响应已保存 {sample_path}",
                "stats": {"sampleFile": str(sample_path), "responses": len(adapter.raw_responses)},
            }

        known_ids = {
            row[0]
            for row in db.query(Alibaba1688Order.external_order_id)
            .filter(Alibaba1688Order.row_status != "deleted")
            .all()
        }
        counters: dict[str, int] = {
            "created": 0, "adopted": 0, "merged": 0,
            "skippedDeleted": 0, "skippedNoId": 0,
        }
        new_order_numbers: set[str] = set()
        consecutive_known = 0
        pages_visited = 0
        raw_seen = 0
        stop_reason = "end"
        lookback_days = max(int(settings.ALIBABA_1688_BROWSER_LOOKBACK_DAYS), 1)
        lookback_cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        batch = _create_virtual_batch(db, actor, row_count=0)
        db.add(batch)
        db.flush()

        for page_responses in adapter.iter_order_pages():
            pages_visited += 1
            page_orders = 0
            page_has_new_order = False
            page_order_times: list[datetime] = []
            seen_on_page: set[str] = set()
            for response in page_responses:
                for raw_order in extract_orders(response):
                    data = map_order(raw_order)
                    order_id = str(data.get("external_order_id") or "").strip()
                    # 同一页的多条 mtop 响应可能包含重复订单，按订单号去重。
                    if order_id and order_id in seen_on_page:
                        continue
                    if order_id:
                        seen_on_page.add(order_id)
                    page_orders += 1
                    raw_seen += 1
                    if not order_id:
                        counters["skippedNoId"] += 1
                        continue
                    if order_id in known_ids:
                        consecutive_known += 1
                    else:
                        page_has_new_order = True
                        consecutive_known = 0
                        known_ids.add(order_id)
                        new_order_numbers.add(order_id)
                    order_time = data.get("order_time")
                    if isinstance(order_time, datetime):
                        if order_time.tzinfo is None:
                            order_time = order_time.replace(tzinfo=timezone.utc)
                        page_order_times.append(order_time)
                    outcome = upsert_order_data(
                        db, data,
                        import_id=batch.id,
                        source="alibaba1688_browser",
                        adopt_from_deleted=True,
                        update_status=True,
                    )
                    if outcome == "skipped_deleted":
                        counters["skippedDeleted"] += 1
                    elif outcome == "skipped_no_id":
                        counters["skippedNoId"] += 1
                    else:
                        counters[outcome] += 1
            if page_orders == 0:
                # 页面响应里没有订单：可能是末页，也可能是登录态/页面结构异常。
                stop_reason = "empty_page"
                break
            page_is_older_than_window = bool(page_order_times) and all(
                order_time < lookback_cutoff for order_time in page_order_times
            )
            if page_is_older_than_window and not page_has_new_order:
                # 已知阈值仍作为兼容性兜底，但不再是唯一的停止条件。
                stop_reason = (
                    "known_threshold"
                    if consecutive_known >= settings.ALIBABA_1688_BROWSER_STOP_AFTER_KNOWN
                    else "lookback_window"
                )
                break
            if consecutive_known >= settings.ALIBABA_1688_BROWSER_STOP_AFTER_KNOWN:
                # 订单通常按时间倒序：连续 N 条都是已知订单，作为保守兜底停止。
                stop_reason = "known_threshold"
                break
        else:
            stop_reason = "last_page" if pages_visited else "empty_page"

        capture_path = _dump_capture_samples(adapter)
        capture_complete = stop_reason != "empty_page"

        if not capture_complete and raw_seen == 0:
            # 没有任何订单时删除空虚拟批次，明确记录为部分失败，避免页面显示“同步成功”。
            db.delete(batch)
            db.commit()
            message = "1688 页面未捕获到订单数据；未写入订单，请检查登录态或页面结构"
            _set_connection(
                db,
                "error",
                message,
                account=account,
                lastSyncAt=_now_iso(),
                lastSyncSummary=message,
                lastCaptureFile=str(capture_path),
            )
            ensure_exception(db, "ALIBABA_1688_BROWSER_EMPTY", "1688 未捕获订单数据", message)
            db.commit()
            return {
                "status": "partial",
                "message": message,
                "stats": {
                    "pagesVisited": pages_visited,
                    "stopReason": stop_reason,
                    "captureComplete": False,
                    "rawCaptureFile": str(capture_path),
                    "batchId": None,
                    **counters,
                },
            }

        batch.row_count = raw_seen
        batch.imported_order_count = counters["created"] + counters["adopted"]
        db.commit()

        # 与 Excel 导入对齐：新订单可能先于入库明细存在，补齐 SKU 分配行。
        from app.services.inbound_allocation_seed import seed_allocations_for_order_numbers

        seed_allocations_for_order_numbers(db, new_order_numbers)

        from app.services.alibaba1688_remark_match_service import run_verified_remark_match

        remark_match = run_verified_remark_match(db, actor=actor)

        summary = (
            f"浏览器直采{'完成' if capture_complete else '部分完成'}：新增 {counters['created']}，"
            f"状态更新 {counters['merged']}，翻页 {pages_visited}（{stop_reason}），批次 #{batch.id}"
        )
        _set_connection(
            db,
            "connected" if capture_complete else "error",
            "" if capture_complete else "本次捕获在空页处提前结束，覆盖可能不完整",
            account=account,
            lastSyncAt=_now_iso(),
            lastSyncSummary=summary,
            lastBatchId=batch.id,
            lastCaptureFile=str(capture_path),
            lastRemarkMatch=remark_match,
        )
        audit(
            db,
            actor,
            "alibaba1688.browser_sync.sync",
            "alibaba1688_file_imports",
            batch.id,
            {
                "pages": pages_visited,
                "stopReason": stop_reason,
                "lookbackDays": lookback_days,
                "captureComplete": capture_complete,
                "remarkMatch": remark_match,
                **counters,
            },
        )
        db.commit()
        return {
            "status": "success" if capture_complete else "partial",
            "message": summary,
            "stats": {
                "pagesVisited": pages_visited,
                "stopReason": stop_reason,
                "lookbackDays": lookback_days,
                "lookbackCutoff": lookback_cutoff.isoformat(),
                "captureComplete": capture_complete,
                "rawCaptureFile": str(capture_path),
                "batchId": batch.id,
                "remarkMatch": remark_match,
                **counters,
            },
        }


def run_login(db: Session, *, actor: str = "system", timeout_s: int | None = None) -> dict[str, Any]:
    """扫码登录入口：在服务器上打开浏览器窗口等待用户扫码，成功后保存登录态。

    必须由 Celery worker 执行（长阻塞，最长 ALIBABA_1688_LOGIN_TIMEOUT_SECONDS）。
    """
    job = start_sync_job(db, PROVIDER, "browser_login")
    try:
        with _ProfileLock():
            with Alibaba1688BrowserAdapter() as adapter:
                account = adapter.wait_for_login(timeout_s)
                # 登录成功立刻导出 storageState 备份（含 session cookie），
                # 供下次启动注入，兜底 Profile cookie 加密/Keychain 异常场景。
                state_path = adapter.export_state()
        _set_connection(
            db, "connected", "",
            account=account, lastLoginCheckAt=_now_iso(),
            **({"stateFile": state_path} if state_path else {}),
        )
        finish_sync_job(db, job, "success", {"account": account or "", "stateExported": bool(state_path)}, "扫码登录成功")
        db.add(SyncLog(provider=PROVIDER, level="info", sync_job_id=job.id, message=f"扫码登录成功（{account or '未知账号'}）"))
        db.commit()
        audit(db, actor, "alibaba1688.browser_sync.login", "integration", None, {"account": account})
        db.commit()
        return {"status": "success", "message": f"扫码登录成功（{account or '未知账号'}）", "account": account}
    except SyncAlreadyRunningError as exc:
        message = str(exc)
        finish_sync_job(db, job, "skipped", {}, message)
        db.add(SyncLog(provider=PROVIDER, level="warn", sync_job_id=job.id, message=message))
        db.commit()
        return {"status": "already_running", "message": message}
    except TimeoutError as exc:
        message = str(exc)
        _set_connection(db, "needs_login", message)
        finish_sync_job(db, job, "failed", {}, message)
        db.add(SyncLog(provider=PROVIDER, level="warn", sync_job_id=job.id, message=message))
        db.commit()
        return {"status": "login_timeout", "message": message}
    except BrowserRiskControlError as exc:
        message = str(exc)
        _set_connection(db, "error", message)
        finish_sync_job(db, job, "failed", {}, message)
        db.add(SyncLog(provider=PROVIDER, level="error", sync_job_id=job.id, message=message))
        db.commit()
        return {"status": "risk_control", "message": message}
