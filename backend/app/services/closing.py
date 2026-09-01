"""月结快照（规格 1.6 / Phase 6）：支持"重新计算某月份"，已发送 V1 保留，修正产生 V2/V3。

- 快照落 closing_versions（period + formula_version + data_version + calculated_at）
- 已发送 V1 不可被后续重算静默覆盖 → 新版本追加，is_current 标记最新
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.audit import audit
from app.models.finance import ClosingVersion
from app.models.payment import ReceivableSnapshot
from app.models.profit import ProfitSnapshot
from app.services import dashboard, profit as profit_service
from app.services import reconciliation as rc


def snapshot_of(db: Session, year: int, month: int, formula_version: str = "v1") -> dict[str, Any]:
    """计算某月快照数据（不落库）。"""
    metrics = dashboard.overview_metrics(db)
    recon = rc.overview(db)
    profit = profit_service.compute(db, year, month)
    return {
        "period": f"{year}-{month:02d}",
        "metrics": metrics,
        "reconciliation": recon,
        "profit": profit,
        "calculatedAt": datetime.now(timezone.utc).isoformat(),
    }


def list_versions(db: Session, year: int | None = None, month: int | None = None) -> list[dict[str, Any]]:
    q = db.query(ClosingVersion)
    if year:
        if month:
            q = q.filter(ClosingVersion.period_id == year * 100 + month)
        else:
            q = q.filter(
                ClosingVersion.period_id >= year * 100 + 1,
                ClosingVersion.period_id <= year * 100 + 12,
            )
    rows = q.order_by(ClosingVersion.period_id.desc(), ClosingVersion.version.desc()).limit(200).all()
    out = []
    for r in rows:
        snap = r.snapshot or {}
        out.append({
            "id": r.id, "periodId": r.period_id, "version": r.version,
            "isCurrent": r.is_current,
            "period": snap.get("period", ""),
            "calculatedAt": snap.get("calculatedAt"),
            "grossProfit": (snap.get("profit") or {}).get("grossProfit"),
            "receivable": (snap.get("reconciliation") or {}).get("receivable"),
            "received": (snap.get("reconciliation") or {}).get("received"),
            "createdAt": r.created_at.isoformat() if r.created_at else None,
        })
    return out


def create_snapshot(db: Session, year: int, month: int, formula_version: str = "v1",
                    actor: str = "lan_user") -> ClosingVersion:
    """月结：计算 → 落 V{n+1} → 标记 current。已发送版本不覆盖，只追加。"""
    if not (1 <= month <= 12):
        raise ValueError("非法月份")
    period_id = year * 100 + month
    snap = snapshot_of(db, year, month, formula_version)

    prev = (
        db.query(ClosingVersion)
        .filter(ClosingVersion.period_id == period_id)
        .order_by(ClosingVersion.version.desc())
        .first()
    )
    version = (prev.version if prev else 0) + 1
    row = ClosingVersion(
        period_id=period_id, version=version, snapshot=snap, is_current=True,
    )
    db.add(row)
    if prev:
        prev.is_current = False
    db.commit()
    audit(db, actor, "closing.snapshot.create", "closing_versions", row.id,
          {"period": f"{year}-{month:02d}", "version": version,
           "formulaVersion": formula_version})
    return row


def recalc(db: Session, year: int, month: int, actor: str = "lan_user") -> ClosingVersion:
    """重新计算某月：V1 保留，产生 V{n+1}（规格 1.6）。"""
    return create_snapshot(db, year, month, formula_version="v1", actor=actor)
