import hashlib
import io
import re
from datetime import datetime, date
from pathlib import Path

from app.config import settings

"""BankFileAdapter —— 浙江农信原始文件安全落盘（规格 1.3 / 8.1 / 10）。

- 原始文件长期保存、只读、不覆盖：同名自动 version 递增
- SHA256 指纹
- 路径: {DATA_DIR}/finance/{company}/{YYYY}/{MM}/original/bank/
- XLSX 解析：通用列名检测（日期/摘要/对方户名/收入/支出/余额/流水号），
  字段名以真实样本核对为准；解析结果注册进 bank_transactions（指纹幂等）。
"""

SAFE_NAME = re.compile(r"[^\w.\-一-龥]+")

# 列名 → 标准字段 的候选匹配（顺序敏感，先精确后包含）
_COLUMN_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("txn_date", ("交易日期", "交易时间", "记账日期", "日期")),
    ("summary", ("摘要", "交易备注", "用途", "备注")),
    ("counterparty", ("对方户名", "对方账户名称", "对方名称", "对方户名/账号", "交易对方")),
    ("counterparty_account", ("对方账号", "对方账户", "对方卡号")),
    ("amount_in", ("收入金额", "贷方发生额", "存入金额", "收入")),
    ("amount_out", ("支出金额", "借方发生额", "支取金额", "支出")),
    ("balance", ("账户余额", "余额", "可用余额")),
    ("voucher_no", ("流水号", "交易流水号", "凭证号", "序号")),
]


def _match_field(header: str) -> str | None:
    h = (header or "").strip()
    if not h:
        return None
    for field, candidates in _COLUMN_PATTERNS:
        for c in candidates:
            if h == c or c in h:
                return field
    return None


def parse_xlsx(content: bytes) -> list[dict]:
    """解析浙江农信交易明细 XLSX。

    返回 [{txn_date, summary, counterparty, counterparty_account,
            amount_in, amount_out, balance, voucher_no}]。
    第一行是表头；自动检测列名；无法识别的列忽略。
    日期支持 datetime / date / 'YYYY-MM-DD' 字符串。
    非 XLSX 内容 / 无关键列 → 返回 []（如实空，不抛异常）。
    """
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:
        return []
    ws = wb.active

    rows_iter = ws.iter_rows(values_only=True)
    header = next(rows_iter, None)
    if header is None:
        wb.close()
        return []

    mapping: dict[str, int] = {}
    for idx, cell in enumerate(header):
        field = _match_field(str(cell) if cell is not None else "")
        if field and field not in mapping:
            mapping[field] = idx

    if "txn_date" not in mapping and "amount_in" not in mapping and "amount_out" not in mapping:
        wb.close()
        return []  # 无关键列，判定不是交易明细表

    out: list[dict] = []
    for row in rows_iter:
        rec: dict = {}
        for field, idx in mapping.items():
            if idx < len(row):
                rec[field] = row[idx]
        if rec.get("txn_date") is None:
            continue  # 合计行/表尾行通常无日期 → 跳过
        if rec.get("amount_in") is None and rec.get("amount_out") is None:
            continue  # 无金额 → 跳过
        out.append(_normalize(rec))
    wb.close()
    return out


def _normalize(rec: dict) -> dict:
    def _num(v) -> str | None:
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return f"{v:.2f}"
        s = str(v).replace(",", "").replace("¥", "").replace(" ", "")
        if not s or s in ("-", "--"):
            return None
        return s

    def _date(v) -> str | None:
        if v is None:
            return None
        if isinstance(v, (datetime, date)):
            return v.strftime("%Y-%m-%d")
        s = str(v).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None

    return {
        "txn_date": _date(rec.get("txn_date")),
        "summary": str(rec.get("summary") or "").strip(),
        "counterparty": str(rec.get("counterparty") or "").strip(),
        "counterparty_account": str(rec.get("counterparty_account") or "").strip(),
        "amount_in": _num(rec.get("amount_in")),
        "amount_out": _num(rec.get("amount_out")),
        "balance": _num(rec.get("balance")),
        "voucher_no": str(rec.get("voucher_no") or "").strip(),
    }


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sanitize_name(name: str) -> str:
    clean = SAFE_NAME.sub("_", name.replace("..", "_"))
    clean = clean.lstrip(".")[:180] or "unnamed"
    return clean


class BankFileAdapter:
    provider = "zhejiang_rural_credit"

    ALLOWED_EXT = {".xlsx", ".xls", ".pdf", ".zip"}

    def save_original(
        self,
        company: str,
        period_year: int,
        period_month: int,
        category: str,
        original_name: str,
        content: bytes,
    ) -> dict:
        ext = Path(original_name).suffix.lower()
        if ext not in self.ALLOWED_EXT:
            raise ValueError(f"不支持的文件类型: {ext}（允许 XLSX/PDF/ZIP）")

        base_dir = (
            Path(settings.DATA_DIR) / "finance" / sanitize_name(company)
            / f"{period_year:04d}" / f"{period_month:02d}" / "original" / category
        )
        base_dir.mkdir(parents=True, exist_ok=True)

        clean = sanitize_name(original_name)
        # 同名不覆盖：version 递增
        version = 1
        target = base_dir / f"{Path(clean).stem}.v{version}{Path(clean).suffix}"
        while target.exists():
            version += 1
            target = base_dir / f"{Path(clean).stem}.v{version}{Path(clean).suffix}"
        target.write_bytes(content)

        return {
            "stored_path": str(target),
            "original_name": original_name,
            "category": category,
            "size": len(content),
            "sha256": sha256_of(target),
            "version": version,
        }
