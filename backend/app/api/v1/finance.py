from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.db import get_db
from app.services import finance_sales_report_service as sales_report_service
from app.services import finance_service
from app.utils.uploads import UploadTooLargeError, read_upload_limited

router = APIRouter(prefix="/finance", tags=["finance"])


class SalesReportFieldInput(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=80)
    enabled: bool = True


class SalesReportTemplateInput(BaseModel):
    company: str = ""
    enabled: bool = True
    fields: list[SalesReportFieldInput] = Field(default_factory=list)
    rules: dict[str, Any] = Field(default_factory=dict)
    to_addrs: list[str] = Field(default_factory=list)
    cc_addrs: list[str] = Field(default_factory=list)
    auto_send: bool = False
    send_day: int = Field(default=3, ge=1, le=28)
    send_hour: int = Field(default=10, ge=0, le=23)


@router.get("/sales-report/template")
def get_sales_report_template(company: str = "", db: Session = Depends(get_db)) -> dict[str, Any]:
    row = sales_report_service.get_or_create_template(
        db, company or finance_service.DEFAULT_COMPANY
    )
    return sales_report_service.serialize_template(row)


@router.put("/sales-report/template")
def update_sales_report_template(
    payload: SalesReportTemplateInput,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        row = sales_report_service.save_template(
            db,
            company=payload.company or finance_service.DEFAULT_COMPANY,
            fields=[field.model_dump() for field in payload.fields],
            rules=payload.rules,
            to_addrs=payload.to_addrs,
            cc_addrs=payload.cc_addrs,
            auto_send=payload.auto_send,
            send_day=payload.send_day,
            send_hour=payload.send_hour,
            enabled=payload.enabled,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return sales_report_service.serialize_template(row)


@router.get("/sales-report/preview")
def preview_sales_report(
    year: int = Query(..., ge=1900, le=2999),
    month: int = Query(..., ge=1, le=12),
    company: str = "",
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    template = sales_report_service.get_or_create_template(
        db, company or finance_service.DEFAULT_COMPANY
    )
    report = sales_report_service.build_report(db, year, month, template)
    return {
        **report,
        "rows": report["rows"][:limit],
        "rowCount": len(report["rows"]),
    }


@router.post("/sales-report/generate")
def generate_sales_report(
    request: Request,
    year: int = Query(..., ge=1900, le=2999),
    month: int = Query(..., ge=1, le=12),
    company: str = "",
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        return sales_report_service.generate_and_archive(
            db,
            company=company or finance_service.DEFAULT_COMPANY,
            year=year,
            month=month,
            actor=current_actor(request),
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/periods")
def list_periods(company: str | None = None, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return finance_service.period_overview(db, company)


@router.post("/files")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    period_year: int = Form(...),
    period_month: int = Form(...),
    category: str = Form(...),
    company: str = Form(""),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """原始资料上传：SHA256 + 版本化归档，同名不覆盖。"""
    try:
        content = await read_upload_limited(file, max_bytes=finance_service.settings.MAX_UPLOAD_BYTES)
        row = finance_service.store_upload(
            db,
            company=company or finance_service.DEFAULT_COMPANY,
            year=period_year, month=period_month,
            category=category, original_name=file.filename or "unnamed",
            content=content, actor=current_actor(request),
        )
    except UploadTooLargeError as exc:
        raise HTTPException(413, str(exc))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    return {
        "id": row.id, "version": row.version, "sha256": row.sha256,
        "size": row.size,
    }


@router.get("/{year}/{month}/files")
def list_files(year: int, month: int, company: str = "", db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    from app.models.finance import ArchiveFile

    try:
        finance_service.validate_period(year, month)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    q = db.query(ArchiveFile).filter_by(period_year=year, period_month=month)
    if company:
        q = q.filter_by(company=company)
    return [
        {
            "id": r.id, "category": r.category, "originalName": r.original_name,
            "size": r.size, "sha256": r.sha256[:16], "version": r.version,
            "uploader": r.uploader,
            "uploadedAt": r.uploaded_at.isoformat() if r.uploaded_at else None,
        }
        for r in q.order_by(ArchiveFile.category, ArchiveFile.original_name, ArchiveFile.version).all()
    ]


@router.get("/files/{file_id}/download")
def download_file(file_id: int, db: Session = Depends(get_db)) -> FileResponse:
    """单文件下载：财务核对原始资料用（归档原文，未改动）。"""
    from pathlib import Path

    from app.models.finance import ArchiveFile

    row = db.get(ArchiveFile, file_id)
    if row is None:
        raise HTTPException(status_code=404, detail="归档文件不存在")
    path = Path(row.stored_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="归档文件在存储中缺失，请联系管理员核对")
    return FileResponse(
        path,
        filename=row.original_name,
        media_type=row.mime or "application/octet-stream",
        headers={"X-Archive-SHA256": row.sha256},
    )


@router.post("/{year}/{month}/check")
def check_period(year: int, month: int, company: str = "", db: Session = Depends(get_db)) -> dict[str, Any]:
    period = finance_service.refresh_period_status(
        db, company or finance_service.DEFAULT_COMPANY, year, month
    )
    return {"status": period.status, "missing": (period.missing_summary or {}).get("missing", {})}


@router.post("/{year}/{month}/package")
def package_period(year: int, month: int, request: Request, company: str = "",
                   db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        pkg = finance_service.package_period(
            db, company or finance_service.DEFAULT_COMPANY, year, month,
            actor=current_actor(request),
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    return {
        "id": pkg.id, "version": pkg.version, "status": pkg.status,
        "sha256": pkg.zip_sha256,
    }


@router.get("/packages/{pkg_id}/download")
def download_package(pkg_id: int, db: Session = Depends(get_db)) -> FileResponse:
    from app.models.finance import FinanceDeliveryPackage

    pkg = db.get(FinanceDeliveryPackage, pkg_id)
    if not pkg:
        raise HTTPException(404, "交付包不存在")
    try:
        zip_path = finance_service.managed_data_file(pkg.zip_path, label="ZIP 文件")
    except RuntimeError as exc:
        raise HTTPException(410, str(exc))
    return FileResponse(str(zip_path), media_type="application/zip", filename=zip_path.name)


class SendBody(BaseModel):
    version: int | None = None
    to_addrs: list[str] = Field(default_factory=list)
    cc_addrs: list[str] = Field(default_factory=list)
    company: str = ""


@router.post("/{year}/{month}/send")
def send(year: int, month: int, body: SendBody, request: Request,
         db: Session = Depends(get_db)) -> dict[str, Any]:
    """发送财务交付包：SMTP 未配置如实失败；已发 V1 重发标记 RESENT。"""
    from app.adapters.base import AdapterNotConfigured

    try:
        result = finance_service.send_delivery(
            db, body.company or finance_service.DEFAULT_COMPANY, year, month,
            version=body.version, to_addrs=body.to_addrs or None, cc_addrs=body.cc_addrs or None,
            actor=current_actor(request),
        )
    except AdapterNotConfigured as exc:
        raise HTTPException(400, str(exc))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, **result}


@router.get("/delivery-logs")
def delivery_logs(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return finance_service.delivery_logs(db)
