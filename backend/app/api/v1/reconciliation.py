from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.db import get_db
from app.models.bank import BankTransaction
from app.models.payment import SettlementRecord
from app.services import reconciliation as rc
from app.services import finance_service
from app.utils.uploads import UploadTooLargeError, read_upload_limited

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


# ---------- 映射规则 ----------

@router.get("/rules")
def list_rules(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [
        {"id": r.id, "matchPattern": r.match_pattern, "matchType": r.match_type,
         "platform": r.platform, "enabled": r.enabled, "note": r.note}
        for r in rc.list_rules(db)
    ]


class RuleBody(BaseModel):
    match_pattern: str = Field(min_length=1, max_length=256)
    match_type: Literal["contains", "equals"] = "contains"
    platform: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=2000)


@router.post("/rules")
def create_rule(body: RuleBody, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        row = rc.add_rule(db, match_pattern=body.match_pattern, match_type=body.match_type,
                          platform=body.platform, note=body.note,
                          actor=current_actor(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": row.id}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    rc.delete_rule(db, rule_id, actor=current_actor(request))
    return {"ok": True}


# ---------- 银行流水 ----------

class TxnBody(BaseModel):
    account_no: str = Field(default="ZJRC-001", min_length=1, max_length=64)
    txn_date: date
    direction: Literal["in", "out"] = "in"
    amount: str = Field(min_length=1, max_length=64)
    counterparty_name: str = Field(default="", max_length=256)
    counterparty_account: str = Field(default="", max_length=128)
    summary: str = Field(default="", max_length=2000)
    voucher_no: str = Field(default="", max_length=128)


@router.get("/transactions")
def list_transactions(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = (
        db.query(BankTransaction)
        .order_by(BankTransaction.txn_date.desc(), BankTransaction.id.desc())
        .limit(limit)
        .all()
    )
    matched_ids = rc.confirmed_txn_ids(db, [r.id for r in rows])
    return [
        {
            "id": r.id, "txnDate": r.txn_date.isoformat(), "direction": r.direction,
            "amount": str(r.amount), "counterpartyName": r.counterparty_name,
            "summary": r.summary, "voucherNo": r.voucher_no,
            "matched": r.id in matched_ids,
        }
        for r in rows
    ]


@router.post("/transactions")
def create_transaction(body: TxnBody, request: Request,
                       db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        row, created = rc.add_transaction(
            db, account_no=body.account_no, txn_date=body.txn_date, direction=body.direction,
            amount=body.amount, counterparty_name=body.counterparty_name,
            counterparty_account=body.counterparty_account, summary=body.summary,
            voucher_no=body.voucher_no, actor=current_actor(request),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": row.id, "created": created,
            "fingerprint": row.fingerprint[:16], "amount": str(row.amount)}


@router.post("/import-bank")
async def import_bank_xlsx(
    request: Request,
    file: UploadFile = File(...),
    account_no: str = Form("ZJRC-001"),
    period_year: int = Form(...),
    period_month: int = Form(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """上传浙江农信交易明细 XLSX → 解析 → 指纹幂等导入流水。"""
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "流水解析仅支持 XLSX 文件；旧版 XLS 请先另存为 XLSX")
    actor = current_actor(request)
    try:
        content = await read_upload_limited(file, max_bytes=finance_service.settings.MAX_UPLOAD_BYTES)
        if not content:
            raise ValueError("空文件")
        archive = finance_service.store_upload(
            db, company=finance_service.DEFAULT_COMPANY,
            year=period_year, month=period_month, category="bank",
            original_name=file.filename or "bank.xlsx", content=content, actor=actor,
        )
        result = rc.import_bank_xlsx(
            db, account_no=account_no, content=content,
            period_year=period_year, period_month=period_month,
            file_name=file.filename or "bank.xlsx", archive_file_id=archive.id,
            actor=actor,
        )
    except UploadTooLargeError as exc:
        raise HTTPException(413, str(exc))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, **result}


# ---------- 应收结算 ----------

class SettlementBody(BaseModel):
    platform: str = Field(min_length=1, max_length=64)
    period_year: int
    period_month: int
    expected_amount: str = Field(min_length=1, max_length=64)
    store_name: str = Field(default="", max_length=256)


@router.get("/settlements")
def list_settlements(
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = db.query(SettlementRecord).order_by(
        SettlementRecord.period_year.desc(), SettlementRecord.period_month.desc()
    ).limit(limit).offset(offset).all()
    settled_amounts = rc.settled_amounts_by_settlement(db, [s.id for s in rows])
    out = []
    for s in rows:
        settled = settled_amounts.get(s.id, 0)
        out.append({
            "id": s.id, "platform": s.platform, "storeName": s.store_name,
            "period": f"{s.period_year}-{s.period_month:02d}",
            "expectedAmount": str(s.expected_amount), "settledAmount": str(settled),
            "status": s.status,
        })
    return out


@router.post("/settlements")
def create_settlement(body: SettlementBody, request: Request,
                      db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        row = rc.add_settlement(db, platform=body.platform, period_year=body.period_year,
                                period_month=body.period_month,
                                expected_amount=body.expected_amount, store_name=body.store_name,
                                actor=current_actor(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": row.id}


# ---------- 匹配 ----------

@router.get("/suggestions")
def get_suggestions(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return rc.suggestions(db)


class ConfirmBody(BaseModel):
    txn_id: int = Field(gt=0)
    settlement_id: int = Field(gt=0)


@router.post("/confirm")
def confirm(body: ConfirmBody, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        row = rc.confirm_match(db, txn_id=body.txn_id, settlement_id=body.settlement_id,
                               actor=current_actor(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": row.id, "score": row.score, "confidence": row.confidence}


@router.post("/reject")
def reject(body: ConfirmBody, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    rc.reject_match(db, txn_id=body.txn_id, settlement_id=body.settlement_id,
                    actor=current_actor(request))
    return {"ok": True}


@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> dict[str, Any]:
    return rc.overview(db)
