"""税务系统官方发票清单导入与台账查询。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.db import get_db
from app.services import tax_invoice_service as service
from app.services.import_lifecycle import LifecycleTransitionError
from app.services.procurement_chain_service import ProcurementChainMatcher
from app.utils.uploads import UploadTooLargeError, read_upload_limited

router = APIRouter(prefix="/tax-invoices", tags=["tax-invoices"])


@router.post("/imports")
async def upload_import(
    request: Request,
    file: UploadFile = File(...),
    period_year: int = Query(0, ge=0, le=9999),
    period_month: int = Query(0, ge=0, le=12),
    auto_confirm: bool = Query(True, description="上传后立即确认并执行采购自动化"),
    db: Session = Depends(get_db),
) -> dict:
    """上传税务系统官方导出的 XLSX/CSV；默认进入 draft 暂存。"""
    try:
        content = await read_upload_limited(file, max_bytes=service.settings.MAX_UPLOAD_BYTES)
        row, duplicate = service.import_export(
            db,
            content=content,
            original_name=file.filename or "tax-invoices.xlsx",
            actor=current_actor(request),
            period_year=period_year,
            period_month=period_month,
            auto_confirm=auto_confirm,
        )
    except UploadTooLargeError as exc:
        raise HTTPException(413, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    automation = None
    if auto_confirm:
        from app.services.procurement_chain_service import run_full_procurement_automation
        automation = run_full_procurement_automation(db, actor=current_actor(request))
    return {
        "duplicate": duplicate,
        "lifecycle": row.lifecycle,
        "import": service.serialize_import(row),
        "automation": automation,
    }


@router.get("/imports")
def list_imports(
    limit: int = Query(50, ge=1, le=200),
    lifecycle: str | None = Query(None, pattern="^(draft|active|deleted)$"),
    db: Session = Depends(get_db),
) -> list[dict]:
    return service.list_imports(db, lifecycle=lifecycle, limit=limit)


@router.get("/imports/{import_id}/records")
def get_import_records(
    import_id: int,
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[dict]:
    return service.list_import_records(db, import_id, limit=limit)


@router.delete("/imports/{import_id}/records/{row_index}", status_code=204)
def delete_import_row(
    request: Request,
    import_id: int,
    row_index: int,
    db: Session = Depends(get_db),
) -> None:
    """明细核对：删除单行（可恢复），对应发票同步从台账隐藏。"""
    try:
        service.delete_row(db, import_id, row_index, actor=current_actor(request))
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.post("/imports/{import_id}/records/{row_index}/restore")
def restore_import_row(
    request: Request,
    import_id: int,
    row_index: int,
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = service.restore_row(db, import_id, row_index, actor=current_actor(request))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return {"rowIndex": row.row_index, "rowStatus": row.row_status}


@router.post("/imports/{import_id}/confirm")
def confirm_import(
    request: Request,
    import_id: int,
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = service.confirm_import(db, import_id, actor=current_actor(request))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except LifecycleTransitionError as exc:
        raise HTTPException(409, str(exc))
    result = service.serialize_import(row)
    from app.services.procurement_chain_service import run_full_procurement_automation
    result["automation"] = run_full_procurement_automation(db, actor=current_actor(request))
    return result


@router.post("/imports/{import_id}/reprocess")
def reprocess_import(
    request: Request,
    import_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """用最新规则重处理已保存批次，并继续执行采购自动化。"""
    try:
        row = service.reprocess_import(db, import_id, actor=current_actor(request))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    from app.services.procurement_chain_service import run_full_procurement_automation
    result = service.serialize_import(row)
    result["automation"] = run_full_procurement_automation(db, actor=current_actor(request))
    return result


@router.delete("/imports/{import_id}", status_code=204)
def soft_delete_import(
    request: Request,
    import_id: int,
    db: Session = Depends(get_db),
) -> None:
    try:
        service.soft_delete_import(db, import_id, actor=current_actor(request))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except LifecycleTransitionError as exc:
        raise HTTPException(409, str(exc))


@router.post("/imports/{import_id}/restore")
def restore_import(
    request: Request,
    import_id: int,
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = service.restore_import(db, import_id, actor=current_actor(request))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except LifecycleTransitionError as exc:
        raise HTTPException(409, str(exc))
    return service.serialize_import(row)


@router.get("/summary")
def get_summary(db: Session = Depends(get_db)) -> dict:
    return service.summary(db)


@router.get("")
def get_invoices(
    direction: str | None = Query(None, pattern="^(input|output|unknown)$"),
    status: str | None = Query(None, pattern="^(issued|void|red|unknown)$"),
    match_status: str | None = Query(None, pattern="^(matched|unmatched|needs_review)$"),
    verified: bool | None = Query(None, description="true=仅已认证 / false=仅未认证"),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[dict]:
    return service.list_invoices(
        db, direction=direction, status=status, match_status=match_status, verified=verified,
        limit=limit, offset=offset,
    )


class BulkVerifyBody(BaseModel):
    invoice_ids: list[int]
    verified: bool
    verified_month: str = ""


@router.post("/bulk-verify")
def bulk_verify_invoices(
    body: BulkVerifyBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """批量标记进项发票勾选认证状态（如税局勾选清单已核对后回填）。

    按发票主键批量调用与单张相同的核验逻辑，幂等；verified_month 形如 2026-08。
    """
    if not body.invoice_ids:
        return {"ok": True, "processed": 0, "items": []}
    matcher = ProcurementChainMatcher(db)
    items: list[dict] = []
    for invoice_id in body.invoice_ids:
        try:
            inv = matcher.set_invoice_verified(invoice_id, body.verified, body.verified_month)
            items.append({"invoiceId": inv.id, "verified": inv.verified, "verifiedMonth": inv.verified_month or ""})
        except ValueError as exc:
            items.append({"invoiceId": invoice_id, "error": str(exc)})
    db.commit()
    return {"ok": True, "processed": len(items), "items": items}
