"""财务资料中心（规格 10）：原始资料 收集→完整性→归档→原样ZIP→留版本。

- 原始文件只读、同名不覆盖（version 递增）
- 缺资料禁止打包（INCOMPLETE → 异常）
- 已生成的 ZIP 版本不可覆盖；已发送 V1 不受后续影响
- SMTP 发送在 Phase 6 配置后开放
"""
from __future__ import annotations

import mimetypes
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.adapters.bank_file import sanitize_name, sha256_of
from app.config import settings
from app.core.audit import audit
from app.models.finance import (
    ArchiveFile,
    FinanceDeliveryFile,
    FinanceDeliveryPackage,
    MonthlyFinancePeriod,
)

DEFAULT_COMPANY = "浙江柴本网络科技有限公司"
CATEGORIES = ("bank", "jackyun", "invoice", "other")
DEFAULT_REQUIRED = {"bank": 1, "invoice": 0, "jackyun": 0, "other": 0}


def validate_period(year: int, month: int) -> None:
    """统一约束账期，避免异常年份进入文件路径或数据库。"""
    if not (1900 <= year <= 2999):
        raise ValueError(f"year 不在合理范围内，给定 {year}")
    if not (1 <= month <= 12):
        raise ValueError("非法月份")


def managed_data_file(path: str | Path, *, label: str) -> Path:
    """仅允许读取 DATA_DIR 内真实存在的普通文件，拒绝越界路径和外链符号链接。"""
    root = Path(settings.DATA_DIR).resolve()
    try:
        resolved = Path(path).resolve(strict=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"{label}缺失（存储被移动或删除）") from exc
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise RuntimeError(f"{label}不在受管数据目录内")
    return resolved


def _write_new_file(target: Path, content: bytes) -> None:
    """用 O_EXCL 语义创建文件，两个并发上传绝不会覆盖彼此。"""
    with target.open("xb") as output:
        output.write(content)


def get_or_create_period(db: Session, company: str, year: int, month: int) -> MonthlyFinancePeriod:
    validate_period(year, month)
    row = (
        db.query(MonthlyFinancePeriod)
        .filter_by(company=company, period_year=year, period_month=month)
        .first()
    )
    if not row:
        row = MonthlyFinancePeriod(
            company=company, period_year=year, period_month=month,
            required_types=DEFAULT_REQUIRED,
        )
        db.add(row)
        db.commit()
    return row


def next_version(db: Session, company: str, year: int, month: int, category: str, original_name: str) -> int:
    cur = (
        db.query(func.max(ArchiveFile.version))
        .filter_by(company=company, period_year=year, period_month=month,
                   category=category, original_name=original_name)
        .scalar()
    )
    return (cur or 0) + 1


def evaluate_completeness(files: list[ArchiveFile], required: dict[str, int]) -> tuple[str, dict[str, Any]]:
    """纯函数：状态判定。缺任何必备类别 → INCOMPLETE。"""
    counts = {c: 0 for c in CATEGORIES}
    for f in files:
        counts[f.category] = counts.get(f.category, 0) + 1
    missing = {}
    for cat, need in (required or {}).items():
        lack = int(need) - counts.get(cat, 0)
        if lack > 0:
            missing[cat] = lack
    return ("READY" if not missing else "INCOMPLETE"), {"counts": counts, "missing": missing}


def refresh_period_status(db: Session, company: str, year: int, month: int) -> MonthlyFinancePeriod:
    period = get_or_create_period(db, company, year, month)
    files = db.query(ArchiveFile).filter_by(company=company, period_year=year, period_month=month).all()
    status, summary = evaluate_completeness(files, period.required_types or DEFAULT_REQUIRED)
    if period.status != "SENT":  # 已发送状态不被自动覆盖
        period.status = status
    period.missing_summary = summary
    db.commit()
    return period


def store_upload(
    db: Session,
    *,
    company: str,
    year: int,
    month: int,
    category: str,
    original_name: str,
    content: bytes,
    actor: str = "system",
) -> ArchiveFile:
    validate_period(year, month)
    if category not in CATEGORIES:
        raise ValueError(f"非法资料类别: {category}")
    if not content:
        raise ValueError("空文件")
    if len(content) > settings.MAX_UPLOAD_BYTES:
        raise ValueError(f"文件超过单文件大小上限（{settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MiB）")

    original_name = original_name or "unnamed"
    version = next_version(db, company, year, month, category, original_name)
    base_dir = (
        Path(settings.DATA_DIR) / "finance" / sanitize_name(company)
        / f"{year:04d}" / f"{month:02d}" / "original" / category
    )
    base_dir.mkdir(parents=True, exist_ok=True)
    clean = sanitize_name(original_name)
    while True:
        target = base_dir / f"{Path(clean).stem}.v{version}{Path(clean).suffix}"
        try:
            _write_new_file(target, content)
            break
        except FileExistsError:
            # 另一个并发上传可能刚写入相同版本；保留原件并尝试下一个版本。
            version += 1

    try:
        mime = mimetypes.guess_type(original_name)[0] or "application/octet-stream"
        row = ArchiveFile(
            company=company, category=category, original_name=original_name,
            stored_path=str(target), size=len(content), mime=mime,
            sha256=sha256_of(target), period_year=year, period_month=month,
            version=version, uploader=actor, uploaded_at=datetime.now(timezone.utc),
        )
        db.add(row)
        db.commit()
    except Exception:
        db.rollback()
        # 仅删除本次用排他方式创建的文件；绝不碰已有版本。
        target.unlink(missing_ok=True)
        raise
    audit(db, actor, "finance.file.upload", "archive_files", row.id,
          {"category": category, "period": f"{year}-{month:02d}", "version": version, "sha256": row.sha256[:16]})
    refresh_period_status(db, company, year, month)
    return row


def package_period(db: Session, company: str, year: int, month: int,
                   actor: str = "system") -> FinanceDeliveryPackage:
    """资料齐全才可打包；每次打包生成新版本号，ZIP 落 output/V{n}，绝不覆盖。"""
    validate_period(year, month)
    period = get_or_create_period(db, company, year, month)
    files = db.query(ArchiveFile).filter_by(company=company, period_year=year, period_month=month).all()
    status, summary = evaluate_completeness(files, period.required_types or DEFAULT_REQUIRED)
    if status != "READY" or not files:
        raise ValueError(f"资料不完整，禁止打包，缺少: {summary['missing']}")

    # 归档表的 stored_path 也属于不可信持久化数据：打包前再次做目录边界校验。
    source_files = [
        (f, managed_data_file(f.stored_path, label="原始归档文件"))
        for f in files
    ]

    prev = (
        db.query(func.max(FinanceDeliveryPackage.version))
        .filter_by(period_id=period.id)
        .scalar()
    )
    version = (prev or 0) + 1
    zip_path: Path | None = None
    created_zip = False
    while True:
        out_dir = (
            Path(settings.DATA_DIR) / "finance" / sanitize_name(company)
            / f"{year:04d}" / f"{month:02d}" / "output" / f"V{version}"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        candidate = out_dir / f"finance_{sanitize_name(company)}_{year:04d}{month:02d}_V{version}.zip"
        try:
            archive = zipfile.ZipFile(candidate, "x", zipfile.ZIP_DEFLATED)
        except FileExistsError:
            version += 1
            continue
        zip_path = candidate
        created_zip = True
        with archive as zf:
            for f, source_path in source_files:  # 原样打包，不做二次加工
                zf.write(source_path, arcname=f"{f.category}/{source_path.name}")
        break

    try:
        pkg = FinanceDeliveryPackage(
            period_id=period.id, version=version, zip_path=str(zip_path),
            zip_sha256=sha256_of(zip_path), status="PACKAGED",
            created_at_src=datetime.now(timezone.utc),
        )
        db.add(pkg)
        db.flush()
        for f in files:
            db.add(FinanceDeliveryFile(package_id=pkg.id, archive_file_id=f.id))
        period.status = "PACKAGED"
        db.commit()
    except Exception:
        db.rollback()
        if created_zip and zip_path is not None:
            zip_path.unlink(missing_ok=True)
        raise
    audit(db, actor, "finance.package.create", "finance_delivery_packages", pkg.id,
          {"version": version, "files": len(files), "sha256": pkg.zip_sha256[:16]})
    return pkg


def period_overview(db: Session, company: str | None = None) -> list[dict[str, Any]]:
    """列出所有账期（来自归档文件与账期表的并集）及状态/交付包。

    查询计划（修复 2N+1）：
    - 1× MonthlyFinancePeriod（按 company 过滤）
    - 1× ArchiveFile 全表或按 company 过滤
    - 1× 聚合取文件计数（GROUP BY company/year/month）
    - 1× FinanceDeliveryPackage JOIN 一次取所有交付包按 company/year/month 分组
    """
    periods_q = (
        db.query(MonthlyFinancePeriod)
        if not company
        else db.query(MonthlyFinancePeriod).filter_by(company=company)
    )
    periods: dict[tuple[str, int, int], dict[str, Any]] = {}
    for row in periods_q.all():
        periods[(row.company, row.period_year, row.period_month)] = {
            "company": row.company, "year": row.period_year, "month": row.period_month,
            "status": row.status, "missing": (row.missing_summary or {}).get("missing", {}),
            "requiredTypes": row.required_types or DEFAULT_REQUIRED,
        }
    archive_q = db.query(ArchiveFile)
    if company:
        archive_q = archive_q.filter_by(company=company)
    for f in archive_q.all():
        key = (f.company, f.period_year, f.period_month)
        if key not in periods:
            periods[key] = {
                "company": f.company, "year": f.period_year, "month": f.period_month,
                "status": "INCOMPLETE", "missing": {}, "requiredTypes": DEFAULT_REQUIRED,
            }

    # 单查询聚合：每个 (company, year, month) 的 ArchiveFile 数量
    file_count_rows = (
        db.query(
            ArchiveFile.company,
            ArchiveFile.period_year,
            ArchiveFile.period_month,
            func.count(ArchiveFile.id),
        )
    )
    if company:
        file_count_rows = file_count_rows.filter(ArchiveFile.company == company)
    file_counts: dict[tuple[str, int, int], int] = {
        (c, y, m): n for c, y, m, n in file_count_rows.group_by(
            ArchiveFile.company, ArchiveFile.period_year, ArchiveFile.period_month
        ).all()
    }

    # 单查询取所有交付包
    pkg_query = (
        db.query(FinanceDeliveryPackage, MonthlyFinancePeriod.company,
                 MonthlyFinancePeriod.period_year, MonthlyFinancePeriod.period_month)
        .join(MonthlyFinancePeriod, FinanceDeliveryPackage.period_id == MonthlyFinancePeriod.id)
        .order_by(FinanceDeliveryPackage.version)
    )
    if company:
        pkg_query = pkg_query.filter(MonthlyFinancePeriod.company == company)
    pkg_groups: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    for pkg, c, y, m in pkg_query.all():
        pkg_groups.setdefault((c, y, m), []).append({
            "id": pkg.id, "version": pkg.version, "status": pkg.status,
            "sha256": pkg.zip_sha256[:16],
            "createdAt": pkg.created_at.isoformat() if pkg.created_at else None,
        })

    out = []
    for key, item in periods.items():
        item["fileCount"] = file_counts.get(key, 0)
        item["packages"] = pkg_groups.get(key, [])
        out.append(item)
    out.sort(key=lambda x: (x["year"], x["month"]), reverse=True)
    return out


def send_delivery(db: Session, company: str, year: int, month: int, *,
                  version: int | None = None, to_addrs: list[str] | None = None,
                  cc_addrs: list[str] | None = None, actor: str = "system") -> dict[str, Any]:
    """发送财务交付包（规格 10 / 16）。

    - SMTP 未配置 → AdapterNotConfigured（如实失败）
    - 一个账期+版本只允许一条"首次成功发送"；再次发送标记 RESENT，不伪装成第一次
    - 成功写 EmailDeliveryLog + audit + 更新 package.status=SENT
    """
    from app.adapters.mail import MailAdapter
    from app.models.finance import EmailDeliveryLog

    # 先检查 SMTP 配置，未配置如实失败（不进入后续逻辑）
    adapter = MailAdapter()
    adapter.ensure_configured()

    period = get_or_create_period(db, company, year, month)
    q = db.query(FinanceDeliveryPackage).filter_by(period_id=period.id)
    if version:
        q = q.filter_by(version=version)
    # 同一交付包的首次发送串行化，配合数据库唯一约束避免并发重复“首次发送”。
    pkg = q.order_by(FinanceDeliveryPackage.version.desc()).with_for_update().first()
    if not pkg:
        raise ValueError("该账期还没有交付包，请先打包")

    zip_path = managed_data_file(pkg.zip_path, label="ZIP 文件")

    to_addrs = to_addrs or list((db.get(MonthlyFinancePeriod, period.id).required_types or {}).get("emails", []) or [])
    if not to_addrs:
        raise ValueError("未配置收件人（需在账期 required_types.emails 或发送时传入 to_addrs）")

    # 幂等：首次成功发送只允许一条
    first_sent = (
        db.query(EmailDeliveryLog)
        .filter_by(package_id=pkg.id, kind="first", status="sent")
        .first()
    )
    kind = "resent" if first_sent else "first"

    subject = f"{company} {year}年{month:02d}月 财务资料 V{version or pkg.version}"
    body = f"见附件 {zip_path.name}\n（原样资料，SHA256: {pkg.zip_sha256}）"
    try:
        message_id = adapter.send(subject, body, to_addrs, cc_addrs or [], [str(zip_path)])
    except Exception as exc:
        log = EmailDeliveryLog(package_id=pkg.id, kind=kind, to_addrs=list(to_addrs),
                               cc_addrs=list(cc_addrs or []), status="failed", error=str(exc)[:2000])
        db.add(log)
        db.commit()
        audit(db, actor, "finance.delivery.send.failed", "email_delivery_logs", log.id,
              {"packageId": pkg.id, "kind": kind, "error": str(exc)[:500]})
        raise RuntimeError(f"邮件发送失败: {exc}") from exc

    log = EmailDeliveryLog(package_id=pkg.id, kind=kind, to_addrs=list(to_addrs),
                           cc_addrs=list(cc_addrs or []), status="sent",
                           message_id=message_id or "")
    db.add(log)
    pkg.status = "SENT"
    if period.status != "SENT":
        period.status = "SENT"
    db.commit()
    audit(db, actor, "finance.delivery.send", "email_delivery_logs", log.id,
          {"packageId": pkg.id, "kind": kind, "to": to_addrs, "messageId": message_id})
    return {"packageId": pkg.id, "version": pkg.version, "kind": kind,
            "messageId": message_id, "sentAt": log.created_at.isoformat() if log.created_at else None}


def delivery_logs(db: Session, company: str | None = None) -> list[dict[str, Any]]:
    from app.models.finance import EmailDeliveryLog, MonthlyFinancePeriod

    q = db.query(EmailDeliveryLog).order_by(EmailDeliveryLog.id.desc()).limit(200)
    out = []
    for r in q.all():
        pkg = db.get(FinanceDeliveryPackage, r.package_id) if r.package_id else None
        period_label = None
        version = None
        if pkg:
            period = db.get(MonthlyFinancePeriod, pkg.period_id) if pkg.period_id else None
            if period:
                period_label = f"{period.period_year}-{period.period_month:02d}"
            version = pkg.version
        out.append({
            "id": r.id, "packageId": r.package_id, "kind": r.kind,
            "toAddrs": r.to_addrs or [], "ccAddrs": r.cc_addrs or [],
            "status": r.status, "messageId": r.message_id, "error": r.error,
            "createdAt": r.created_at.isoformat() if r.created_at else None,
            "period": period_label, "version": version,
        })
    return out
