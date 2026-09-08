from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.db import get_db
from app.services.production_finished_flow_service import (
    FINISHED_MOVEMENT_TYPES,
    inbound_candidates,
    link_inbound_allocations,
    list_finished_movements,
    list_inbound_allocations,
    production_finished_flow,
    record_finished_movement,
)


router = APIRouter(prefix="/supply-chain", tags=["supply-chain-finished-flow"])


class FinishedFlowItemInput(BaseModel):
    production_order_item_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0)


class FinishedMovementInput(BaseModel):
    request_key: str = Field(min_length=8, max_length=64)
    carrier: str = Field(default="", max_length=128)
    tracking_no: str = Field(default="", max_length=128)
    note: str = Field(default="", max_length=2000)
    items: list[FinishedFlowItemInput] = Field(min_length=1, max_length=200)


class InboundAllocationItemInput(BaseModel):
    production_order_item_id: int = Field(gt=0)
    inbound_item_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0)


class InboundAllocationInput(BaseModel):
    request_key: str = Field(min_length=8, max_length=64)
    items: list[InboundAllocationItemInput] = Field(min_length=1, max_length=200)


def _movement_items(payload: FinishedMovementInput) -> list[dict[str, Any]]:
    return [
        {
            "production_order_item_id": item.production_order_item_id,
            "quantity": item.quantity,
        }
        for item in payload.items
    ]


@router.get("/production-orders/{order_id}/finished-flow")
def finished_flow(order_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return production_finished_flow(db, order_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/finished-movements")
def finished_movements(
    order_id: int | None = Query(None, gt=0),
    movement_type: str = Query("", max_length=24),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        rows = list_finished_movements(
            db,
            order_id=order_id,
            movement_type=movement_type,
            limit=limit,
        )
        return {"rows": rows, "count": len(rows)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/production-inbound-allocations")
def inbound_allocations(
    order_id: int | None = Query(None, gt=0),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    rows = list_inbound_allocations(db, order_id=order_id, limit=limit)
    return {"rows": rows, "count": len(rows)}


@router.get("/production-orders/{order_id}/inbound-candidates")
def production_inbound_candidates(
    order_id: int,
    limit: int = Query(300, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        return inbound_candidates(db, order_id, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _record(
    *,
    order_id: int,
    movement_type: str,
    payload: FinishedMovementInput,
    request: Request,
    db: Session,
) -> dict[str, Any]:
    if movement_type not in FINISHED_MOVEMENT_TYPES:
        raise HTTPException(status_code=400, detail="成品流转类型不正确")
    try:
        rows = record_finished_movement(
            db,
            order_id=order_id,
            movement_type=movement_type,
            items=_movement_items(payload),
            actor=current_actor(request),
            request_key=payload.request_key,
            carrier=payload.carrier,
            tracking_no=payload.tracking_no,
            note=payload.note,
        )
        return {"rows": rows, "movementNo": rows[0]["movementNo"] if rows else None}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/production-orders/{order_id}/finished/complete")
def complete_finished_goods(
    order_id: int,
    payload: FinishedMovementInput,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _record(
        order_id=order_id,
        movement_type="complete",
        payload=payload,
        request=request,
        db=db,
    )


@router.post("/production-orders/{order_id}/finished/ship")
def ship_finished_goods(
    order_id: int,
    payload: FinishedMovementInput,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _record(
        order_id=order_id,
        movement_type="ship",
        payload=payload,
        request=request,
        db=db,
    )


@router.post("/production-orders/{order_id}/finished/arrive")
def arrive_finished_goods(
    order_id: int,
    payload: FinishedMovementInput,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _record(
        order_id=order_id,
        movement_type="arrive",
        payload=payload,
        request=request,
        db=db,
    )


@router.post("/production-orders/{order_id}/inbound-links")
def link_production_inbound(
    order_id: int,
    payload: InboundAllocationInput,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        rows = link_inbound_allocations(
            db,
            order_id=order_id,
            items=[
                {
                    "production_order_item_id": item.production_order_item_id,
                    "inbound_item_id": item.inbound_item_id,
                    "quantity": item.quantity,
                }
                for item in payload.items
            ],
            actor=current_actor(request),
            request_key=payload.request_key,
        )
        return {"rows": rows, "count": len(rows)}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
