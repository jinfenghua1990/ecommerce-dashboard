from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.jackyun import JackyunAdapter, finish_sync_job, start_sync_job
from app.adapters.base import AdapterError, AdapterNotConfigured
from app.config import settings
from app.core.audit import audit
from app.models.integration import IntegrationConnection
from app.models.ops import ExceptionRecord

PROVIDERS = [
    {"id": "jackyun_mcp", "name": "吉客云 MCP", "mode": "服务端 Token", "phase": 1},
    {"id": "jackyun_openapi", "name": "吉客 OpenAPI", "mode": "服务端 AppKey/AppSecret", "phase": 1},
    {"id": "alibaba_1688", "name": "1688 买家订单", "mode": "OAuth 只读授权", "phase": 4},
    {"id": "zhejiang_rural_credit", "name": "浙江农信", "mode": "Excel/PDF 文件导入", "phase": 3},
    {"id": "smtp", "name": "财务邮件", "mode": "人工确认后 SMTP 发送", "phase": 6},
]


def get_or_create_connection(db: Session, provider: str, mode: str = "", phase: int = 1) -> IntegrationConnection:
    row = db.query(IntegrationConnection).filter_by(provider=provider, mode=mode).first()
    if not row:
        row = IntegrationConnection(provider=provider, mode=mode, phase=phase)
        db.add(row)
        db.commit()
    return row


def integration_status(db: Session) -> list[dict[str, Any]]:
    """页面刷新只读本地库，不触发外部查询（规格 5）。"""
    jackyun_ready = settings.jackyun_configured
    alibaba_ready = settings.alibaba_1688_configured
    smtp_ready = settings.smtp_configured
    rows: list[dict[str, Any]] = []
    for p in PROVIDERS:
        pid = p["id"]
        conn = get_or_create_connection(db, pid, p["mode"], p["phase"])
        status = conn.status
        if pid == "jackyun_mcp":
            status = conn.status if conn.status in ("connected", "error") else ("untested" if jackyun_ready else "unconfigured")
        elif pid == "jackyun_openapi":
            status = "unconfigured"
        elif pid == "alibaba_1688":
            status = conn.status if conn.status in ("connected", "error") else ("untested" if alibaba_ready else "unconfigured")
        elif pid == "zhejiang_rural_credit":
            status = "available"  # 文件导入模式天然可用，无需凭证
        elif pid == "smtp":
            status = conn.status if conn.status in ("connected", "error") else ("untested" if smtp_ready else "unconfigured")
        rows.append({
            "id": pid,
            "name": p["name"],
            "mode": p["mode"],
            "phase": p["phase"],
            "status": status,
            "lastTestedAt": conn.last_tested_at.isoformat() if conn.last_tested_at else None,
            "lastSuccessAt": conn.last_success_at.isoformat() if conn.last_success_at else None,
            "errorSummary": conn.error_summary or None,
        })
    return rows


def test_jackyun(db: Session) -> dict[str, Any]:
    """真实调用 MCP：initialize → tools/list。成功/失败都写连接状态+审计+异常（失败时）。"""
    conn = get_or_create_connection(db, "jackyun_mcp", "服务端 Token", 1)
    conn.last_tested_at = datetime.now(timezone.utc)
    job = start_sync_job(db, "jackyun", "connection_test")
    try:
        adapter = JackyunAdapter(db)
        result = adapter.test_connection()
        conn.status = "connected"
        conn.last_success_at = datetime.now(timezone.utc)
        conn.error_summary = ""
        conn.meta = {"tools": result["tools"], "serverInfo": result["server_info"]}
        finish_sync_job(db, job, "success", {"tools_count": len(result["tools"])})
        audit(db, "lan_user", "jackyun.connection_test.success", "integration", conn.id,
              {"tools": result["tools"]})
        return {"ok": True, **result}
    except (AdapterNotConfigured, AdapterError, NotImplementedError) as exc:
        msg = str(exc)
        conn.status = "error"
        conn.error_summary = msg[:500]
        finish_sync_job(db, job, "failed", {}, msg)
        db.add(SyncLogRow(provider="jackyun", level="error", message=msg, sync_job_id=job.id))
        ensure_exception(db, "JACKYUN_SYNC_FAIL", "吉客云同步失败", msg)
        db.commit()
        audit(db, "lan_user", "jackyun.connection_test.failed", "integration", conn.id, {"error": msg})
        return {"ok": False, "error": msg}


# 避免循环导入的小包装
def SyncLogRow(**kw):  # noqa: N802
    from app.models.integration import SyncLog

    return SyncLog(**kw)


def ensure_exception(db: Session, code: str, title: str, detail: str) -> None:
    exists = (
        db.query(ExceptionRecord)
        .filter(ExceptionRecord.code == code, ExceptionRecord.status.in_(("pending", "confirmed")))
        .first()
    )
    if exists:
        exists.detail = {"latest": detail[:2000]}
    else:
        db.add(ExceptionRecord(code=code, type=code, title=title, detail={"latest": detail[:2000]},
                               severity="high", source="system"))
    db.commit()
