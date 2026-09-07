from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.core.audit import audit
from app.db import get_db
from app.models.ops import ExceptionRecord

router = APIRouter(prefix="/exceptions", tags=["exceptions"])

ALLOWED = {"pending", "confirmed", "ignored", "resolved"}


class StatusBody(BaseModel):
    status: str
    note: str = ""


@router.get("")
def list_exceptions(
    status: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    q = db.query(ExceptionRecord).order_by(ExceptionRecord.id.desc())
    if status:
        q = q.filter(ExceptionRecord.status == status)
    rows = q.limit(limit).offset(offset).all()
    return [
        {
            "id": r.id, "code": r.code, "type": r.type, "severity": r.severity,
            "title": r.title, "detail": r.detail, "status": r.status,
            "createdAt": r.created_at.isoformat() if r.created_at else None,
            "handledBy": r.handled_by, "note": r.note,
        }
        for r in rows
    ]


@router.post("/{exc_id}/status")
def change_status(exc_id: int, body: StatusBody, request: Request,
                  db: Session = Depends(get_db)) -> dict[str, Any]:
    if body.status not in ALLOWED:
        raise HTTPException(400, f"非法状态: {body.status}")
    row = db.get(ExceptionRecord, exc_id)
    if not row:
        raise HTTPException(404, "异常不存在")
    row.status = body.status
    actor = current_actor(request)
    row.handled_by = actor
    row.handled_at = datetime.now(timezone.utc)
    row.note = body.note
    db.commit()
    audit(db, actor, f"exception.{body.status}", "exceptions", exc_id, {"note": body.note})
    return {"ok": True, "id": exc_id, "status": row.status}
