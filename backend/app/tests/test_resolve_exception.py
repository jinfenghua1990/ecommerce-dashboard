"""故障恢复后自动关闭异常（resolve_exception）的回归测试。"""

from __future__ import annotations

from app.models.ops import ExceptionRecord
from app.services.integration_service import ensure_exception, resolve_exception


def _pending_codes(db):
    return {
        row.code
        for row in db.query(ExceptionRecord).filter_by(status="pending").all()
    }


def test_resolve_closes_pending_and_keeps_confirmed(db_session):
    ensure_exception(db_session, "JACKYUN_SYNC_FAIL", "吉客云同步失败", "旧故障")
    # 人工确认的记录不应被自动关闭
    confirmed = (
        db_session.query(ExceptionRecord)
        .filter_by(code="JACKYUN_SYNC_FAIL", status="pending")
        .first()
    )
    confirmed.status = "confirmed"
    db_session.commit()

    closed = resolve_exception(db_session, "JACKYUN_SYNC_FAIL", "恢复自动关闭")

    assert closed == 0
    assert (
        db_session.query(ExceptionRecord)
        .filter_by(code="JACKYUN_SYNC_FAIL", status="confirmed")
        .count()
        == 1
    )


def test_resolve_closes_pending_rows_with_audit(db_session):
    ensure_exception(db_session, "ALIBABA_1688_BROWSER_CAPTURE_FAIL", "1688 捕获失败", "旧故障")
    row = (
        db_session.query(ExceptionRecord)
        .filter_by(code="ALIBABA_1688_BROWSER_CAPTURE_FAIL", status="pending")
        .first()
    )
    assert row is not None

    closed = resolve_exception(db_session, "ALIBABA_1688_BROWSER_CAPTURE_FAIL", "恢复自动关闭")

    assert closed == 1
    db_session.refresh(row)
    assert row.status == "resolved"
    assert row.handled_by == "system"
    assert row.handled_at is not None
    assert "自动关闭" in (row.note or "")
    assert "ALIBABA_1688_BROWSER_CAPTURE_FAIL" not in _pending_codes(db_session)


def test_resolve_unknown_code_is_noop(db_session):
    assert resolve_exception(db_session, "NOT_EXIST_CODE", "无此异常") == 0
