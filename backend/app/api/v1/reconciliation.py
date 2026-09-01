from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.bank import BankTransaction
from app.models.payment import SettlementRecord
from app.services import reconciliation as rc

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


# ---------- 映射规则 ----------

@router.get("/rules")
def list_rules(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rc.seed_rules_if_empty(db)
    return [
        {"id": r.id, "matchPattern": r.match_pattern, "matchType": r.match_type,
         "platform": r.platform, "enabled": r.enabled, "note": r.note}
        for r in rc.list_rules(db)
    ]


class RuleBody(BaseModel):
    match_pattern: str
    match_type: str = "contains"
    platform: str
    note: str = ""


@router.post("/rules")
def create_rule(body: RuleBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        row = rc.add_rule(db, match_pattern=body.match_pattern, match_type=body.match_type,
                          platform=body.platform, note=body.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": row.id}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    rc.delete_rule(db, rule_id)
    return {"ok": True}


# ---------- 银行流水 ----------

class TxnBody(BaseModel):
    account_no: str = "ZJRC-001"
    txn_date: date
    direction: str = "in"
    amount: str
    counterparty_name: str = ""
    counterparty_account: str = ""
    summary: str = ""
    voucher_no: str = ""


@router.get("/transactions")
def list_transactions(limit: int = 100, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (
        db.query(BankTransaction)
        .order_by(BankTransaction.txn_date.desc(), BankTransaction.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id, "txnDate": r.txn_date.isoformat(), "direction": r.direction,
            "amount": str(r.amount), "counterpartyName": r.counterparty_name,
            "summary": r.summary, "voucherNo": r.voucher_no,
            "matched": rc.has_confirmed_match(db, r.id),
        }
        for r in rows
    ]


@router.post("/transactions")
def create_transaction(body: TxnBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        row, created = rc.add_transaction(
            db, account_no=body.account_no, txn_date=body.txn_date, direction=body.direction,
            amount=body.amount, counterparty_name=body.counterparty_name,
            counterparty_account=body.counterparty_account, summary=body.summary,
            voucher_no=body.voucher_no,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": row.id, "created": created,
            "fingerprint": row.fingerprint[:16], "amount": str(row.amount)}


@router.post("/import-bank")
async def import_bank_xlsx(
    file: UploadFile = File(...),
    account_no: str = Form("ZJRC-001"),
    period_year: int = Form(...),
    period_month: int = Form(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """上传浙江农信交易明细 XLSX → 解析 → 指纹幂等导入流水。"""
    if not (file.filename or "").lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "仅支持 XLSX/XLS 文件")
    content = await file.read()
    if not content:
        raise HTTPException(400, "空文件")
    try:
        result = rc.import_bank_xlsx(
            db, account_no=account_no, content=content,
            period_year=period_year, period_month=period_month,
        )
    except Exception as exc:
        raise HTTPException(400, f"解析失败: {exc}")
    return {"ok": True, **result}


# ---------- 应收结算 ----------

class SettlementBody(BaseModel):
    platform: str
    period_year: int
    period_month: int
    expected_amount: str
    store_name: str = ""


@router.get("/settlements")
def list_settlements(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    out = []
    for s in db.query(SettlementRecord).order_by(
        SettlementRecord.period_year.desc(), SettlementRecord.period_month.desc()
    ).all():
        settled = rc.settled_amount_of(db, s.id)
        out.append({
            "id": s.id, "platform": s.platform, "storeName": s.store_name,
            "period": f"{s.period_year}-{s.period_month:02d}",
            "expectedAmount": str(s.expected_amount), "settledAmount": str(settled),
            "status": s.status,
        })
    return out


@router.post("/settlements")
def create_settlement(body: SettlementBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        row = rc.add_settlement(db, platform=body.platform, period_year=body.period_year,
                                period_month=body.period_month,
                                expected_amount=body.expected_amount, store_name=body.store_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": row.id}


# ---------- 匹配 ----------

@router.get("/suggestions")
def get_suggestions(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rc.seed_rules_if_empty(db)
    return rc.suggestions(db)


class ConfirmBody(BaseModel):
    txn_id: int
    settlement_id: int


@router.post("/confirm")
def confirm(body: ConfirmBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        row = rc.confirm_match(db, txn_id=body.txn_id, settlement_id=body.settlement_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": row.id, "score": row.score, "confidence": row.confidence}


@router.post("/reject")
def reject(body: ConfirmBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    rc.reject_match(db, txn_id=body.txn_id, settlement_id=body.settlement_id)
    return {"ok": True}


@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> dict[str, Any]:
    return rc.overview(db)
