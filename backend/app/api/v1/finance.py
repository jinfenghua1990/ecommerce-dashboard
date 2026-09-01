from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import finance_service

router = APIRouter(prefix="/finance", tags=["finance"])


@router.get("/periods")
def list_periods(company: str | None = None, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return finance_service.period_overview(db, company)


@router.post("/files")
async def upload_file(
    file: UploadFile = File(...),
    period_year: int = Form(...),
    period_month: int = Form(...),
    category: str = Form(...),
    company: str = Form(""),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """原始资料上传：SHA256 + 版本化归档，同名不覆盖。"""
    content = await file.read()
    try:
        row = finance_service.store_upload(
            db,
            company=company or finance_service.DEFAULT_COMPANY,
            year=period_year, month=period_month,
            category=category, original_name=file.filename or "unnamed",
            content=content,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    return {
        "id": row.id, "version": row.version, "sha256": row.sha256,
        "storedPath": row.stored_path, "size": row.size,
    }


@router.get("/{year}/{month}/files")
def list_files(year: int, month: int, company: str = "", db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    from app.models.finance import ArchiveFile

    if month < 1 or month > 12:
        raise HTTPException(status_code=422, detail=f"month 必须在 1..12，给定 {month}")
    if year < 1900 or year > 2999:
        raise HTTPException(status_code=422, detail=f"year 不在合理范围内，给定 {year}")

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


@router.post("/{year}/{month}/check")
def check_period(year: int, month: int, company: str = "", db: Session = Depends(get_db)) -> dict[str, Any]:
    period = finance_service.refresh_period_status(
        db, company or finance_service.DEFAULT_COMPANY, year, month
    )
    return {"status": period.status, "missing": (period.missing_summary or {}).get("missing", {})}


@router.post("/{year}/{month}/package")
def package_period(year: int, month: int, company: str = "", db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        pkg = finance_service.package_period(
            db, company or finance_service.DEFAULT_COMPANY, year, month
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    return {
        "id": pkg.id, "version": pkg.version, "status": pkg.status,
        "zipPath": pkg.zip_path, "sha256": pkg.zip_sha256,
    }


@router.get("/packages/{pkg_id}/download")
def download_package(pkg_id: int, db: Session = Depends(get_db)) -> FileResponse:
    from app.models.finance import FinanceDeliveryPackage

    pkg = db.get(FinanceDeliveryPackage, pkg_id)
    if not pkg:
        raise HTTPException(404, "交付包不存在")
    import os

    if not os.path.exists(pkg.zip_path):
        raise HTTPException(410, "ZIP 文件缺失（存储被移动或删除）")
    return FileResponse(pkg.zip_path, media_type="application/zip",
                        filename=pkg.zip_path.split("/")[-1])


class SendBody(BaseModel):
    version: int | None = None
    to_addrs: list[str] = []
    cc_addrs: list[str] = []
    company: str = ""


@router.post("/{year}/{month}/send")
def send(year: int, month: int, body: SendBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    """发送财务交付包：SMTP 未配置如实失败；已发 V1 重发标记 RESENT（规格 16）。"""
    from app.adapters.base import AdapterNotConfigured

    try:
        result = finance_service.send_delivery(
            db, body.company or finance_service.DEFAULT_COMPANY, year, month,
            version=body.version, to_addrs=body.to_addrs or None, cc_addrs=body.cc_addrs or None,
        )
    except AdapterNotConfigured as exc:
        raise HTTPException(400, str(exc))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, **result}


@router.get("/delivery-logs")
def delivery_logs(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return finance_service.delivery_logs(db)
