from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.core.audit import audit
from app.db import get_db
from app.services import warehouse_service as svc

router = APIRouter(prefix="/warehouses", tags=["仓库配置"])


class WarehouseCreateBody(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    warehouse_type: str = "other"
    purpose: str = "both"
    is_sellable: bool = False
    status: str = "active"
    note: str = ""


class WarehouseUpdateBody(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    warehouse_type: str | None = None
    purpose: str | None = None
    is_sellable: bool | None = None
    status: str | None = None
    note: str | None = None


@router.get("")
def list_rows(
    include_inactive: bool = Query(True),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return svc.list_warehouses(db, include_inactive=include_inactive)


@router.post("")
def create_row(
    body: WarehouseCreateBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        row = svc.create_warehouse(db, **body.model_dump())
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    audit(db, current_actor(request), "warehouse.create", "warehouses", row.id, {"code": row.code})
    return svc.serialize(row)


@router.patch("/{warehouse_id}")
def update_row(
    warehouse_id: int,
    body: WarehouseUpdateBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        values = {key: value for key, value in body.model_dump().items() if value is not None}
        row = svc.update_warehouse(db, warehouse_id, **values)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    audit(
        db,
        current_actor(request),
        "warehouse.update",
        "warehouses",
        row.id,
        {"fields": sorted(values.keys()), "status": row.status},
    )
    return svc.serialize(row)
