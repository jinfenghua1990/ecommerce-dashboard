from typing import Any

from sqlalchemy.orm import Session

from app.models.org import AuditLog


def audit(
    db: Session,
    actor: str,
    action: str,
    object_type: str = "",
    object_id: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    """所有调整/状态变更必须写审计日志（规格 1.6 / 11 / 12）。"""
    db.add(
        AuditLog(
            actor=actor or "system",
            action=action,
            object_type=object_type,
            object_id=str(object_id or ""),
            detail=detail or {},
        )
    )
    db.commit()
