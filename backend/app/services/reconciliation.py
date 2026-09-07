"""回款匹配框架（规格 8.2 / 16）。

- 银行匹配不能只靠金额：平台 + 日期 + 金额 + 对方户名 + 摘要 + 流水号综合评分
- 对方户名映射规则可配置，规则变更必须写审计日志，不静默重写历史匹配
- 输出 confidence + matched target + status
- 幂等指纹：账户+日期+金额+流水号；流水号为空用可重复 hash fallback
"""
from __future__ import annotations

import calendar
import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.models.bank import BankAccount, BankTransaction, CounterpartyMappingRule
from app.models.payment import ReconciliationMatch, SettlementRecord
from app.utils.money import quantize, to_decimal

# 规格 8.2 示例默认规则
DEFAULT_RULES = [
    ("江苏银行-平台交易资金专户（抖音）", "抖音"),
    ("上海得物信息集团有限公司", "得物"),
    ("平安银行电子商务交易资金待清算专户（得物）", "得物"),
    ("上海寻梦信息技术有限公司", "拼多多"),
]


# ---------- 纯函数（可单测） ----------

def build_fingerprint(account_no: str, txn_date: str, amount, voucher_no: str,
                      counterparty_name: str = "", summary: str = "") -> str:
    """账户+日期+金额+流水号；流水号为空用可重复 hash fallback（规格 16）。"""
    amt = f"{quantize(to_decimal(amount), Decimal('0.01')):f}"
    if voucher_no:
        raw = f"{account_no}|{txn_date}|{amt}|{voucher_no}"
    else:
        raw = f"{account_no}|{txn_date}|{amt}|{counterparty_name}|{summary}"
    return hashlib.sha256(raw.encode()).hexdigest()


def match_platform(counterparty_name: str, rules: list[tuple[str, str, str]]) -> str | None:
    """rules: [(pattern, match_type, platform)]，contains 优先级按规则顺序。"""
    name = counterparty_name or ""
    for pattern, match_type, platform in rules:
        if match_type == "equals" and name == pattern:
            return platform
        if match_type != "equals" and pattern in name:
            return platform
    return None


def _period_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def score_match(*, txn_date: date, txn_amount, counterparty_name: str, summary: str,
                settlement_platform: str, settlement_store: str,
                settlement_expected, settlement_year: int, settlement_month: int,
                platform_of_txn: str | None) -> dict[str, Any]:
    """综合评分：金额(40) + 平台规则(30) + 日期(15) + 户名/摘要提示(10)。"""
    reasons: list[str] = []
    score = 0

    amt = to_decimal(txn_amount)
    exp = to_decimal(settlement_expected)
    if exp > 0 and abs(amt - exp) <= Decimal("0.01"):
        score += 40
        reasons.append("金额精确一致 +40")
    elif exp > 0 and abs(amt - exp) <= exp * Decimal("0.01"):
        score += 20
        reasons.append("金额误差<1% +20")

    if platform_of_txn and platform_of_txn == settlement_platform:
        score += 30
        reasons.append(f"对方户名命中平台规则[{settlement_platform}] +30")

    pe = _period_end(settlement_year, settlement_month)
    days = abs((txn_date - pe).days)
    if txn_date.year == settlement_year and txn_date.month == settlement_month:
        score += 15
        reasons.append("交易在结算账期内 +15")
    elif days <= 7:
        score += 10
        reasons.append("交易距账期末≤7天 +10")

    hint = settlement_store or settlement_platform or ""
    if hint and (hint in (counterparty_name or "") or (summary and hint in summary)):
        score += 10
        reasons.append("户名/摘要含店铺或平台提示 +10")

    confidence = "high" if score >= 80 else ("medium" if score >= 55 else "low")
    return {"score": score, "confidence": confidence, "reasons": reasons}


# ---------- DB 层 ----------

def seed_rules_if_empty(db: Session) -> bool:
    if db.query(CounterpartyMappingRule).count() > 0:
        return False
    for pattern, platform in DEFAULT_RULES:
        db.add(CounterpartyMappingRule(match_pattern=pattern, match_type="contains", platform=platform,
                                       note="规格 8.2 默认规则"))
    db.commit()
    audit(db, "system", "reconciliation.rules.seed", "counterparty_mapping_rules", "",
          {"count": len(DEFAULT_RULES)})
    return True


def list_rules(db: Session) -> list[CounterpartyMappingRule]:
    return db.query(CounterpartyMappingRule).order_by(CounterpartyMappingRule.id).all()


def active_rule_tuples(db: Session) -> list[tuple[str, str, str]]:
    return [(r.match_pattern, r.match_type, r.platform)
            for r in list_rules(db) if r.enabled]


def add_rule(db: Session, *, match_pattern: str, match_type: str, platform: str,
             note: str = "", actor: str = "system") -> CounterpartyMappingRule:
    if match_type not in ("contains", "equals"):
        raise ValueError("非法匹配类型")
    if not match_pattern or not platform:
        raise ValueError("匹配模式与平台名必填")
    row = CounterpartyMappingRule(match_pattern=match_pattern, match_type=match_type,
                                  platform=platform, note=note)
    db.add(row)
    db.commit()
    audit(db, actor, "reconciliation.rule.create", "counterparty_mapping_rules", row.id,
          {"pattern": match_pattern, "platform": platform})
    return row


def delete_rule(db: Session, rule_id: int, actor: str = "system") -> None:
    row = db.get(CounterpartyMappingRule, rule_id)
    if row:
        db.delete(row)
        db.commit()
        audit(db, actor, "reconciliation.rule.delete", "counterparty_mapping_rules", rule_id,
              {"pattern": row.match_pattern, "platform": row.platform})


def ensure_account(db: Session, account_no: str, account_name: str = "") -> BankAccount:
    row = db.query(BankAccount).filter_by(account_no=account_no).first()
    if not row:
        row = BankAccount(account_no=account_no, account_name=account_name or account_no,
                          bank_name="浙江农信")
        db.add(row)
        db.commit()
    return row


def add_transaction(db: Session, *, account_no: str, txn_date: date, direction: str,
                    amount, counterparty_name: str = "", counterparty_account: str = "",
                    summary: str = "", voucher_no: str = "",
                    import_batch_id: int | None = None,
                    actor: str = "system") -> tuple[BankTransaction, bool]:
    """登记银行流水。指纹幂等：重复返回已有记录（created=False）。"""
    if direction not in ("in", "out"):
        raise ValueError("方向必须为 in/out")
    account = ensure_account(db, account_no)
    fp = build_fingerprint(account_no, txn_date.isoformat(), amount, voucher_no,
                           counterparty_name, summary)
    existing = db.query(BankTransaction).filter_by(fingerprint=fp).first()
    if existing:
        return existing, False
    row = BankTransaction(
        account_id=account.id, import_batch_id=import_batch_id,
        txn_date=txn_date, direction=direction,
        amount=quantize(to_decimal(amount), Decimal("0.01")),
        counterparty_name=counterparty_name or "", counterparty_account=counterparty_account or "",
        summary=summary or "", voucher_no=voucher_no or "", fingerprint=fp,
    )
    db.add(row)
    db.commit()
    audit(db, actor, "bank.txn.create", "bank_transactions", row.id,
          {"date": txn_date.isoformat(), "amount": str(row.amount), "counterparty": counterparty_name})
    return row, True


def import_bank_xlsx(db: Session, *, account_no: str, content: bytes,
                     period_year: int, period_month: int,
                     file_name: str, archive_file_id: int,
                     actor: str = "system") -> dict:
    """解析浙江农信 XLSX 并幂等导入流水（规格 8.1 / 16）。

    - 解析结果逐行 add_transaction（指纹幂等，重复跳过）
    - 记录 BankImportBatch 归档元信息
    返回 {"parsed": N, "created": N, "duplicates": N, "skipped": N}
    """
    from datetime import date

    from app.adapters.bank_file import parse_xlsx
    from app.models.bank import BankImportBatch

    if not (1 <= period_month <= 12):
        raise ValueError("非法账期")
    batch = BankImportBatch(
        source="zjrc", file_name=file_name, archive_file_id=archive_file_id,
        period_year=period_year, period_month=period_month,
        row_count=0, status="processing",
    )
    db.add(batch)
    db.commit()

    rows = parse_xlsx(content)
    if not rows:
        batch.status = "failed"
        db.commit()
        audit(db, actor, "bank.import.xlsx.failed", "bank_import_batches", batch.id,
              {"archiveFileId": archive_file_id, "reason": "未识别到交易明细"})
        raise ValueError("原始文件已归档，但未识别到交易明细；请确认是 XLSX 流水文件及表头")
    created = duplicates = skipped = 0
    for r in rows:
        if not r.get("txn_date"):
            skipped += 1
            continue
        try:
            amount_in = to_decimal(r["amount_in"]) if r.get("amount_in") is not None else None
            amount_out = to_decimal(r["amount_out"]) if r.get("amount_out") is not None else None
        except (TypeError, ValueError):
            skipped += 1
            continue
        if amount_in is not None and amount_in > 0:
            direction, amount = "in", amount_in
        elif amount_out is not None and amount_out > 0:
            direction, amount = "out", amount_out
        else:
            skipped += 1
            continue
        try:
            txn_date = date.fromisoformat(r["txn_date"])
            _, is_new = add_transaction(
                db, account_no=account_no, txn_date=txn_date, direction=direction,
                amount=amount, counterparty_name=r.get("counterparty") or "",
                counterparty_account=r.get("counterparty_account") or "",
                summary=r.get("summary") or "", voucher_no=r.get("voucher_no") or "",
                import_batch_id=batch.id, actor=actor,
            )
        except (TypeError, ValueError):
            skipped += 1
            continue
        if is_new:
            created += 1
        else:
            duplicates += 1

    batch.row_count = created + duplicates
    batch.status = "done"
    db.commit()
    audit(db, actor, "bank.import.xlsx", "bank_import_batches", batch.id,
          {"account": account_no, "parsed": len(rows), "created": created,
           "duplicates": duplicates, "skipped": skipped,
           "period": f"{period_year}-{period_month:02d}",
           "archiveFileId": archive_file_id})
    return {"batchId": batch.id, "archiveFileId": archive_file_id,
            "parsed": len(rows), "created": created,
            "duplicates": duplicates, "skipped": skipped}


def add_settlement(db: Session, *, platform: str, period_year: int, period_month: int,
                   expected_amount, store_name: str = "",
                   actor: str = "system") -> SettlementRecord:
    if not (1 <= period_month <= 12):
        raise ValueError("非法账期")
    row = SettlementRecord(
        platform=platform, store_name=store_name or "",
        period_year=period_year, period_month=period_month,
        expected_amount=quantize(to_decimal(expected_amount), Decimal("0.01")),
        source="manual", status="open",
    )
    db.add(row)
    db.commit()
    audit(db, actor, "settlement.create", "settlement_records", row.id,
          {"platform": platform, "period": f"{period_year}-{period_month:02d}",
           "expected": str(row.expected_amount)})
    return row


def settled_amounts_by_settlement(
    db: Session,
    settlement_ids: list[int] | None = None,
) -> dict[int, Decimal]:
    """一次聚合取各应收的已确认入账，避免列表和总览逐行查询流水。"""
    if settlement_ids is not None and not settlement_ids:
        return {}
    q = (
        db.query(
            ReconciliationMatch.target_id,
            func.coalesce(func.sum(BankTransaction.amount), Decimal("0")),
        )
        .join(BankTransaction, BankTransaction.id == ReconciliationMatch.txn_id)
        .filter(
            ReconciliationMatch.target_type == "settlement",
            ReconciliationMatch.status == "confirmed",
            BankTransaction.direction == "in",
        )
        .group_by(ReconciliationMatch.target_id)
    )
    if settlement_ids is not None:
        q = q.filter(ReconciliationMatch.target_id.in_(settlement_ids))
    return {int(target_id): to_decimal(total) for target_id, total in q.all()}


def settled_amount_of(db: Session, settlement_id: int) -> Decimal:
    return settled_amounts_by_settlement(db, [settlement_id]).get(settlement_id, Decimal("0"))


def refresh_settlement_status(db: Session, settlement: SettlementRecord) -> None:
    settled = settled_amount_of(db, settlement.id)
    expected = to_decimal(settlement.expected_amount)
    if expected > 0 and settled >= expected:
        settlement.status = "settled"
    elif settled > 0:
        settlement.status = "partial"
    else:
        settlement.status = "open"
    db.commit()


def _txn_platform(
    db: Session,
    txn: BankTransaction,
    rules: list[tuple[str, str, str]] | None = None,
) -> str | None:
    return match_platform(txn.counterparty_name, rules if rules is not None else active_rule_tuples(db))


def has_confirmed_match(db: Session, txn_id: int) -> bool:
    return (
        db.query(ReconciliationMatch)
        .filter_by(txn_id=txn_id, status="confirmed")
        .count()
    ) > 0


def confirmed_txn_ids(db: Session, txn_ids: list[int]) -> set[int]:
    if not txn_ids:
        return set()
    return {
        int(txn_id)
        for (txn_id,) in (
            db.query(ReconciliationMatch.txn_id)
            .filter(ReconciliationMatch.status == "confirmed", ReconciliationMatch.txn_id.in_(txn_ids))
            .distinct()
            .all()
        )
    }


def suggest_for_txn(
    db: Session,
    txn: BankTransaction,
    top: int = 3,
    *,
    rules: list[tuple[str, str, str]] | None = None,
    settlements: list[SettlementRecord] | None = None,
) -> list[dict[str, Any]]:
    platform_of_txn = _txn_platform(db, txn, rules)
    results = []
    candidates = settlements if settlements is not None else (
        db.query(SettlementRecord).filter(SettlementRecord.status != "settled").all()
    )
    for s in candidates:
        r = score_match(
            txn_date=txn.txn_date, txn_amount=txn.amount,
            counterparty_name=txn.counterparty_name, summary=txn.summary,
            settlement_platform=s.platform, settlement_store=s.store_name,
            settlement_expected=s.expected_amount,
            settlement_year=s.period_year, settlement_month=s.period_month,
            platform_of_txn=platform_of_txn,
        )
        results.append({"settlement": s, **r})
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top]


def suggestions(db: Session, limit: int = 50) -> list[dict[str, Any]]:
    """未确认流水 → 最佳匹配建议（不落库，确认才生效）。"""
    txns = (
        db.query(BankTransaction)
        .filter(BankTransaction.direction == "in")
        .order_by(BankTransaction.txn_date.desc())
        .limit(200)
        .all()
    )
    confirmed_ids = confirmed_txn_ids(db, [txn.id for txn in txns])
    rules = active_rule_tuples(db)
    open_settlements = db.query(SettlementRecord).filter(SettlementRecord.status != "settled").all()
    out = []
    for txn in txns:
        if txn.id in confirmed_ids:
            continue
        best = suggest_for_txn(db, txn, top=1, rules=rules, settlements=open_settlements)
        if not best or best[0]["score"] <= 0:
            continue
        s = best[0]["settlement"]
        out.append({
            "txnId": txn.id, "txnDate": txn.txn_date.isoformat(),
            "counterparty": txn.counterparty_name, "amount": str(txn.amount),
            "settlementId": s.id, "platform": s.platform,
            "period": f"{s.period_year}-{s.period_month:02d}",
            "expectedAmount": str(s.expected_amount),
            "score": best[0]["score"], "confidence": best[0]["confidence"],
            "reasons": best[0]["reasons"],
        })
        if len(out) >= limit:
            break
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def confirm_match(db: Session, *, txn_id: int, settlement_id: int,
                  actor: str = "system") -> ReconciliationMatch:
    txn = db.get(BankTransaction, txn_id)
    settlement = db.get(SettlementRecord, settlement_id)
    if not txn or not settlement:
        raise ValueError("流水或应收记录不存在")
    if txn.direction != "in":
        raise ValueError("仅入账流水可确认为回款")
    confirmed = (
        db.query(ReconciliationMatch)
        .filter_by(txn_id=txn_id, status="confirmed")
        .first()
    )
    if confirmed:
        if confirmed.target_type == "settlement" and confirmed.target_id == settlement_id:
            raise ValueError("该匹配已确认")
        raise ValueError("该银行流水已确认到其他目标，不可重复确认")
    rules = active_rule_tuples(db)
    best = suggest_for_txn(db, txn, top=5, rules=rules)
    hit = next((b for b in best if b["settlement"].id == settlement_id), None)
    score = hit["score"] if hit else 0
    confidence = hit["confidence"] if hit else "low"
    platform_of_txn = _txn_platform(db, txn, rules)
    row = ReconciliationMatch(
        txn_id=txn_id, target_type="settlement", target_id=settlement_id,
        score=score, confidence=confidence, status="confirmed",
        matched_platform=platform_of_txn or settlement.platform, matched_by="manual",
    )
    db.add(row)
    txn.matched_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("该银行流水已被其他操作确认，请刷新后查看") from exc
    refresh_settlement_status(db, settlement)
    audit(db, actor, "reconciliation.match.confirm", "reconciliation_matches", row.id,
          {"txnId": txn_id, "settlementId": settlement_id, "score": score,
           "confidence": confidence, "amount": str(txn.amount)})
    return row


def reject_match(db: Session, *, txn_id: int, settlement_id: int,
                 actor: str = "system") -> None:
    row = (
        db.query(ReconciliationMatch)
        .filter_by(txn_id=txn_id, target_type="settlement", target_id=settlement_id)
        .first()
    )
    if row:
        was_confirmed = row.status == "confirmed"
        row.status = "rejected"
        db.flush()
        if was_confirmed and not has_confirmed_match(db, txn_id):
            txn = db.get(BankTransaction, txn_id)
            if txn:
                txn.matched_at = None
        db.commit()
    settlement = db.get(SettlementRecord, settlement_id)
    if settlement:
        refresh_settlement_status(db, settlement)
    audit(db, actor, "reconciliation.match.reject", "reconciliation_matches", row.id if row else "",
          {"txnId": txn_id, "settlementId": settlement_id})


def overview(db: Session) -> dict[str, Any]:
    """应回款 / 已回款 / 待回款 + 分平台。页面刷新只查本地库。"""
    settlements = db.query(SettlementRecord).all()
    settled_amounts = settled_amounts_by_settlement(db, [s.id for s in settlements])
    receivable = Decimal("0")
    per_platform: dict[str, dict[str, Decimal]] = {}
    for s in settlements:
        settled = settled_amounts.get(s.id, Decimal("0"))
        expected = to_decimal(s.expected_amount)
        receivable += expected
        agg = per_platform.setdefault(s.platform, {"expected": Decimal("0"), "settled": Decimal("0")})
        agg["expected"] += expected
        agg["settled"] += settled
    received = sum((v["settled"] for v in per_platform.values()), Decimal("0"))
    return {
        "receivable": f"{receivable:f}",
        "received": f"{received:f}",
        "pending": f"{receivable - received:f}",
        "byPlatform": {
            p: {"expected": f"{v['expected']:f}", "settled": f"{v['settled']:f}"}
            for p, v in per_platform.items()
        },
    }
