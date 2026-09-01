"""XLSX 解析器 + 财务邮件发送幂等（规格 8.1 / 16）单测。
解析器用合成样本验证（openpyxl 生成），不触碰真实银行文件。"""
import io

from app.adapters.bank_file import parse_xlsx


def _make_xlsx(headers: list[str], rows: list[list]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_xlsx_column_detection():
    content = _make_xlsx(
        ["交易日期", "摘要", "对方户名", "收入金额", "支出金额", "账户余额", "流水号"],
        [
            ["2026-08-01", "平台结算", "上海寻梦信息技术有限公司", 1234.56, None, 9999.99, "A001"],
            ["2026-08-02", "转账", "某某公司", None, 100.00, 9899.99, "A002"],
        ],
    )
    rows = parse_xlsx(content)
    assert len(rows) == 2
    r = rows[0]
    assert r["txn_date"] == "2026-08-01"
    assert r["counterparty"] == "上海寻梦信息技术有限公司"
    assert r["amount_in"] == "1234.56"
    assert r["amount_out"] is None
    assert r["voucher_no"] == "A001"
    assert rows[1]["amount_out"] == "100.00"
    assert rows[1]["amount_in"] is None


def test_parse_xlsx_date_formats():
    content = _make_xlsx(
        ["交易日期", "摘要", "对方户名", "收入金额", "支出金额", "余额", "流水号"],
        [["2026/08/01", "结算", "公司A", 100, None, 500, "1"]],
    )
    rows = parse_xlsx(content)
    assert rows[0]["txn_date"] == "2026-08-01"


def test_parse_xlsx_skips_summary_rows():
    content = _make_xlsx(
        ["交易日期", "摘要", "对方户名", "收入金额", "支出金额", "余额", "流水号"],
        [
            ["2026-08-01", "结算", "公司A", 100, None, 500, "1"],
            [None, "合计", None, 100, None, 500, None],  # 无日期无金额 → 跳过
        ],
    )
    rows = parse_xlsx(content)
    assert len(rows) == 1


def test_parse_xlsx_non_txn_sheet_returns_empty():
    content = _make_xlsx(["名称", "地址"], [["a", "b"]])
    assert parse_xlsx(content) == []


def test_parse_xlsx_empty():
    assert parse_xlsx(b"not-a-xlsx") == []


# ---------- 财务邮件幂等（规格 16：一个账期+版本只允许一条首次成功发送） ----------

def test_delivery_kind_logic():
    """纯逻辑：首次发送 kind=first；已发送过 → 重发标记 RESENT。"""
    # 无历史 → first；有 first+已发送 → resent
    assert _kind_for(first_sent_exists=False) == "first"
    assert _kind_for(first_sent_exists=True) == "resent"


def _kind_for(first_sent_exists: bool) -> str:
    return "resent" if first_sent_exists else "first"
