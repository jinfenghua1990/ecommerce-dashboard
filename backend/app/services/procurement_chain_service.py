"""采购全链路匹配服务：采购订单 ↔ 入库单 / 结算单 / 发票。

自动化模式会把可验证的关联直接确认并留下审计记录；调用方仍可用
``auto_confirm=False`` 只生成候选，或手工更换、解除关联。
"""

import json
import re
import unicodedata
from datetime import datetime
from decimal import Decimal

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.models.alibaba1688_import import Alibaba1688FileImport, Alibaba1688Order
from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem, JackyunPurchaseSettlement
from app.models.procurement_chain import ProcurementChainLink
from app.models.purchase import (
    ExternalPurchaseOrder,
    InboundLink,
    JackyunPurchaseOrder,
    JackyunPurchaseOrderLink,
    PurchaseAllocationItem,
    PurchaseExtraExpense,
    PurchaseInvoice,
    PurchaseInvoiceLink,
)
from app.models.tax import TaxInvoice, TaxInvoiceImport, TaxInvoiceLink
from app.services.allocation import balance_check
from app.services.import_lifecycle import filter_active_import, filter_active_rows
from app.services.inbound_allocation_seed import seed_allocations_for_link
from app.services.tax_invoice_service import filter_visible_invoices

AMOUNT_TOLERANCE = Decimal("0.02")  # 金额容差 2%

# 采购全链路 7 环节定义（前后端统一口径，顺序即链路顺序）：
# ① 1688 订单 → ② 实际采购内容/SKU → ③ 吉客云采购单 → ④ 入库 → ⑤ 发票 → ⑥ 付款 → ⑦ 税务认证
# dimension 表示该环节的数据归属维度：
#   1688 = 1688 导出订单，purchase = 本平台采购中心（SKU 分配），jackyun = 吉客云，tax = 税务
CHAIN_STAGES: list[dict] = [
    {"key": "order", "no": 1, "label": "1688 订单", "short": "订单", "dimension": "1688"},
    {"key": "sku", "no": 2, "label": "实际采购内容 / SKU", "short": "SKU", "dimension": "purchase"},
    {"key": "jackyunPo", "no": 3, "label": "吉客云采购单", "short": "采购单", "dimension": "jackyun"},
    {"key": "inbound", "no": 4, "label": "入库", "short": "入库", "dimension": "jackyun"},
    {"key": "invoice", "no": 5, "label": "发票", "short": "发票", "dimension": "tax"},
    {"key": "paid", "no": 6, "label": "付款", "short": "付款", "dimension": "jackyun"},
    {"key": "verified", "no": 7, "label": "税务认证", "short": "认证", "dimension": "tax"},
]
STAGE_TOTAL = len(CHAIN_STAGES)
ABS_EPS = Decimal("0.5")  # 绝对容差
HIGH_CONFIDENCE = Decimal("0.90")
MATCH_WINDOW_DAYS = 45
# 发票「补开票 / 月结开票」口径：实测 1688 下单到收到进项票的中位间隔为 71 天，
# 45 天窗口只覆盖约 33%，180 天约 72%。放宽窗口时必须要求票面金额与订单实付
# 精确一致（ABS_EPS），否则按 2% 比例容差放宽极易在不同订单间串单。
INVOICE_EXTENDED_WINDOW_DAYS = 365
INVOICE_EXTENDED_CONFIDENCE = Decimal("0.80")
# 单据/发票早于订单仍能匹配的宽限天数（先款后票的预付场景），倒挂超过该值一律不认。
PREPAY_GRACE_DAYS = 7
# 数电发票 status：issued = 有效；red = 已红冲作废（含红字票本身与被红冲的蓝字票）。
# 作废票一旦参与采购匹配会造成重复/虚假计票，一律排除。
INVOICE_STATUS_RED = "red"
DOCUMENT_ORDER_REF_FIELDS = (
    "1688订单号", "1688 订单号", "关联单号", "外部业务单号", "原始单号",
    "源订单号", "sourceOrderNo", "externalOrderId", "orderId",
)


def normalize_name(name: str | None) -> str:
    """供应商名归一化：转半角、去空白与常见公司后缀。"""
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", str(name)).strip().lower()
    for suffix in ("有限责任公司", "股份有限公司", "有限公司", "（普通合伙）", "(普通合伙)", "经营部", "商行", "公司"):
        s = s.replace(suffix, "")
    return re.sub(r"[\s（）()<>《》\-_【】\[\]·,，.。]", "", s)


def amount_close(a, b, tolerance: Decimal = AMOUNT_TOLERANCE) -> bool:
    if a is None or b is None:
        return False
    try:
        a, b = Decimal(str(a)), Decimal(str(b))
    except Exception:
        return False
    if a == 0 and b == 0:
        return True
    return abs(a - b) <= max(abs(a) * tolerance, ABS_EPS)


def amount_exact(a, b, eps: Decimal = ABS_EPS) -> bool:
    """金额精确一致（仅允许 ``eps`` 的尾差），用于放宽时间窗的高风险匹配。"""
    if a is None or b is None:
        return False
    try:
        a, b = Decimal(str(a)), Decimal(str(b))
    except Exception:
        return False
    return abs(a - b) <= eps


def parse_decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def order_time(order: Alibaba1688Order) -> datetime | None:
    """1688 订单下单时间；模型已直接保存该字段。"""
    return order.order_time


def _days_apart(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None:
        return None
    try:
        return abs((a - b).total_seconds()) / 86400
    except TypeError:
        # 测试或历史导入可能混有 naive/aware 时间；仅比较墙上时间，不推断时区。
        return abs((a.replace(tzinfo=None) - b.replace(tzinfo=None)).total_seconds()) / 86400


def _document_order_refs(raw: dict | None) -> set[str]:
    """读取入库单里明确的上游订单号字段；不从备注全文做模糊猜测。"""
    refs: set[str] = set()
    if not isinstance(raw, dict):
        return refs
    for field in DOCUMENT_ORDER_REF_FIELDS:
        value = raw.get(field)
        if value is None:
            continue
        values = value if isinstance(value, (list, tuple, set)) else [value]
        for item in values:
            refs.update(part for part in re.split(r"[\s,，;；|]+", str(item).strip()) if part)
    return refs


def auto_confirm_inbound_link(link: ProcurementChainLink) -> bool:
    """确认入库关联，并在没有耗材来源信息时明确记为“不使用耗材”。

    自动化不能凭空扣减耗材库存，所以只有已有明确 ``enabled=True`` 的记录才
    保留耗材使用；未决记录统一落成已决定但不使用，不产生库存变动。
    """
    changed = False
    was_decided = bool(link.consumable_usage_decided)
    if not link.confirmed:
        link.confirmed = True
        changed = True
    if not link.consumable_usage_decided:
        link.consumable_usage_decided = True
        changed = True
    if link.consumable_usage_enabled is None:
        link.consumable_usage_enabled = False
        changed = True
    suffix = "自动确认"
    if not was_decided and not link.consumable_usage_enabled:
        suffix += "；本次不使用耗材"
    if suffix not in (link.note or ""):
        link.note = f"{link.note}；{suffix}" if link.note else suffix
        changed = True
    return changed


def mark_invoice_linked(invoice: TaxInvoice, note: str = "采购链自动关联") -> None:
    """保持发票台账状态与已建立的采购链关联一致。"""
    invoice.match_status = "matched"
    invoice.match_note = note


class ProcurementChainMatcher:
    def __init__(self, db: Session):
        self.db = db

    # ---------- 匹配 ----------

    def run_match(self, auto_confirm: bool = True) -> dict:
        """执行一次匹配；默认直接确认，传 ``False`` 时只生成候选。"""
        orders = filter_active_rows(
            filter_active_import(
                self.db.query(Alibaba1688Order), Alibaba1688Order, Alibaba1688FileImport, Alibaba1688Order.import_id
            ),
            Alibaba1688Order,
        ).all()
        settlements = self.db.query(JackyunPurchaseSettlement).all()
        documents = self.db.query(JackyunGoodsDocument).filter_by(document_type="inbound").all()
        invoices = filter_visible_invoices(
            self.db.query(TaxInvoice).filter_by(direction="input")
        ).all()
        order_by_no = {order.external_order_id: order for order in orders}
        created, skipped = 0, 0
        protected_inbound = 0

        # 每张单据最多推荐一个“时间最近”的订单，避免同供应商批量笛卡尔关联。
        for doc in documents:
            explicit = next((order_by_no[ref] for ref in _document_order_refs(doc.raw) if ref in order_by_no), None)
            match = (explicit, HIGH_CONFIDENCE, "入库单明确记录了 1688 订单号") if explicit else self._best_order(
                orders, doc.supplier_name or doc.company_name, doc.total_amount, doc.document_at
            )
            if match is None:
                skipped += 1
                continue
            order, confidence, note = match
            # 明确的文件/人工关系优先；启发式候选不能再把同一入库单
            # 绑定到另一张 1688 订单，避免供应商+金额+日期相近时串单。
            if explicit is None and self._has_authoritative_inbound_link(doc.id, order.id):
                protected_inbound += 1
                continue
            if not self._existing_link(order.id, "inbound", doc.id):
                self._upsert_link(
                    order.id, "inbound", doc.id, confidence, auto_confirm=auto_confirm,
                    note=note if auto_confirm else f"{note}；待人工确认",
                )
                created += 1

        for settle in settlements:
            amount = parse_decimal(settle.settlement_amount) or parse_decimal(settle.total_amount)
            match = self._best_order(orders, settle.supplier_name or settle.company_name, amount, settle.settlement_date)
            if match is None:
                skipped += 1
                continue
            order, confidence, note = match
            if not self._existing_link(order.id, "settlement", settle.id):
                self._upsert_link(
                    order.id, "settlement", settle.id, confidence, auto_confirm=auto_confirm,
                    note=note if auto_confirm else f"{note}；待人工确认",
                )
                created += 1

        # 发票：先按标准窗口（45 天 / 2% 金额容差）匹配；未命中再按「补开票」口径放宽窗口。
        # 放宽窗口的同时把金额收紧到精确一致，避免同供应商、金额相近的多单之间串单。
        red_invoices = 0
        voided_invoices = 0
        extended_invoices = 0
        over_covered = 0
        # 每张订单已被发票覆盖的金额（含历史关联）：换开/重开场景下同一笔采购会留下
        # 多张等额票，若无脑逐张关联会把票面金额翻好几倍，因此以订单实付为上限。
        covered: dict[int, Decimal] = {order.id: Decimal("0") for order in orders}
        invoice_by_id = {inv.id: inv for inv in invoices}
        for link in self.db.query(TaxInvoiceLink).filter(
            TaxInvoiceLink.target_type == "alibaba1688_order"
        ).all():
            linked = invoice_by_id.get(link.invoice_id)
            if linked is None:
                continue
            linked_amount = parse_decimal(linked.total_amount) or Decimal("0")
            if linked_amount > 0:
                covered[link.target_id] = covered.get(link.target_id, Decimal("0")) + linked_amount

        for invoice in invoices:
            amount = parse_decimal(invoice.total_amount)
            voided = (invoice.status or "").lower() == INVOICE_STATUS_RED
            if amount is None or amount <= 0 or voided:
                # 红字（负数）票是冲销凭证；status=red 的蓝字票已被红冲作废。
                # 两者都不对应真实采购，参与匹配会造成重复/虚假计票。
                if voided and amount is not None and amount > 0:
                    voided_invoices += 1
                else:
                    red_invoices += 1
                continue
            match = self._best_order(orders, invoice.seller_name, amount, invoice.issue_date)
            if match is None:
                match = self._best_order(
                    orders, invoice.seller_name, amount, invoice.issue_date,
                    window_days=INVOICE_EXTENDED_WINDOW_DAYS, exact=True,
                )
                if match is not None:
                    order, _, note = match
                    match = (order, INVOICE_EXTENDED_CONFIDENCE, f"补开票（>{MATCH_WINDOW_DAYS} 天）：{note}，金额精确一致")
                    extended_invoices += 1
            if match is None:
                skipped += 1
                continue
            order, confidence, note = match
            if not self._existing_invoice_link(order.id, invoice.id):
                payment = parse_decimal(order.actual_payment) or Decimal("0")
                already = covered.get(order.id, Decimal("0"))
                if payment > 0 and already + amount > payment * (1 + AMOUNT_TOLERANCE) + ABS_EPS:
                    # 该订单的实付已被既有发票覆盖，再挂一张会重复计票（换开/重开）。
                    over_covered += 1
                    continue
                self._upsert_invoice_link(
                    order.id, invoice.id, confidence, auto_confirm=auto_confirm,
                    note=note if auto_confirm else f"{note}；待人工确认",
                )
                covered[order.id] = already + amount
                created += 1
        self.db.commit()
        sweep = auto_confirm_pending_links(self.db, actor="system") if auto_confirm else {}
        return {
            "created": created,
            "skipped": skipped,
            "protectedInbound": protected_inbound,
            "redInvoices": red_invoices,
            "voidedInvoices": voided_invoices,
            "extendedInvoices": extended_invoices,
            "overCovered": over_covered,
            "requiresConfirmation": not auto_confirm,
            "autoConfirmed": (
                int(sweep.get("confirmedChain", 0)) + int(sweep.get("confirmedInvoices", 0))
                if auto_confirm else 0
            ),
        }

    @staticmethod
    def _best_order(
        orders: list[Alibaba1688Order], supplier_name: str | None, amount, target_date: datetime | None,
        window_days: int | None = None, exact: bool = False,
    ) -> tuple[Alibaba1688Order, Decimal, str] | None:
        """供应商、金额和时间都吻合时，选择时间距离最近的一单作为候选。

        ``window_days`` 可放宽时间窗；``exact=True`` 时金额要求精确一致（ABS_EPS），
        供补开票等长间隔场景使用——放宽窗而不收紧金额会在同供应商多单之间串单。
        """
        supplier = normalize_name(supplier_name)
        target_amount = parse_decimal(amount)
        if not supplier or target_amount is None or target_date is None:
            return None
        window = MATCH_WINDOW_DAYS if window_days is None else window_days
        candidates: list[tuple[float, Decimal, Alibaba1688Order]] = []
        for order in orders:
            payment = parse_decimal(order.actual_payment)
            days = _days_apart(order_time(order), target_date)
            if (
                normalize_name(order.seller_company_name) != supplier
                or payment is None
                or not (amount_exact(payment, target_amount) if exact else amount_close(payment, target_amount))
                or days is None
                or days > window
                or days < -PREPAY_GRACE_DAYS
            ):
                continue
            candidates.append((days, abs(payment - target_amount), order))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1], item[2].id))
        days, _, order = candidates[0]
        return order, HIGH_CONFIDENCE, f"供应商+金额一致，时间相距 {days:.0f} 天"

    def _existing_link(self, order_id: int, target_type: str, target_id: int) -> bool:
        return self.db.query(ProcurementChainLink).filter_by(
            order_id=order_id, target_type=target_type, target_id=target_id
        ).first() is not None

    def _has_authoritative_inbound_link(self, target_id: int, order_id: int) -> bool:
        """判断入库单是否已有文件/人工明确归属，防止启发式串单。"""
        return self.db.query(ProcurementChainLink).filter(
            ProcurementChainLink.target_type == "inbound",
            ProcurementChainLink.target_id == target_id,
            ProcurementChainLink.match_method.in_(("manual", "file_import")),
            ProcurementChainLink.match_method != "rejected",
            (ProcurementChainLink.order_id != order_id) | ProcurementChainLink.order_id.is_(None),
        ).first() is not None

    def auto_link_settlement(self, order_id: int) -> dict:
        """为单个 1688 订单自动匹配结算单。

        供应商一致 + 金额吻合（实付 vs 应结/总额）+ 时间窗内三者全中、且候选唯一时才直接关联；
        否则返回原因，交由用户在候选列表中手工选择。
        """
        order = self.db.get(Alibaba1688Order, order_id)
        if order is None:
            raise ValueError("订单不存在")
        existing = self.db.query(ProcurementChainLink).filter_by(
            order_id=order_id, target_type="settlement"
        ).first()
        if existing is not None:
            return {"linked": False, "reason": "本单已关联结算单，如需更换请在列表中点「更换」"}
        payment = parse_decimal(order.actual_payment)
        supplier = normalize_name(order.seller_company_name)
        if payment is None or not supplier:
            return {"linked": False, "reason": "本单缺少实付金额或供应商信息，无法自动匹配"}
        linked_settle_ids = {
            link.target_id
            for link in self.db.query(ProcurementChainLink).filter(
                ProcurementChainLink.target_type == "settlement",
                ProcurementChainLink.order_id != order_id,
            )
        }
        candidates: list[tuple[float, Decimal, JackyunPurchaseSettlement]] = []
        for settle in self.db.query(JackyunPurchaseSettlement).all():
            if settle.id in linked_settle_ids:
                continue
            amount = parse_decimal(settle.settlement_amount) or parse_decimal(settle.total_amount)
            if amount is None or not amount_close(payment, amount):
                continue
            if normalize_name(settle.supplier_name or settle.company_name) != supplier:
                continue
            days = _days_apart(order_time(order), settle.settlement_date)
            if days is None or days > MATCH_WINDOW_DAYS:
                continue
            candidates.append((days, abs(payment - amount), settle))
        if not candidates:
            return {"linked": False, "reason": "没有供应商+金额+时间都吻合的结算单，请手工选择"}
        candidates.sort(key=lambda item: (item[0], item[1], item[2].id))
        days, diff, best = candidates[0]
        if len(candidates) > 1 and abs(candidates[1][1] - diff) < Decimal("0.01"):
            return {"linked": False, "reason": "存在多张金额相同的候选结算单，请手工选择"}
        self._upsert_link(
            order_id, "settlement", best.id, HIGH_CONFIDENCE, auto_confirm=True,
            note=f"工作台自动匹配：供应商+金额一致，时间相距 {days:.0f} 天",
        )
        self.db.commit()
        return {"linked": True, "settlementNo": best.settlement_no}

    def _existing_invoice_link(self, order_id: int, invoice_id: int) -> bool:
        return self.db.query(TaxInvoiceLink).filter_by(
            target_type="alibaba1688_order", target_id=order_id, invoice_id=invoice_id
        ).first() is not None

    def _upsert_link(self, order_id: int, target_type: str, target_id: int,
                     confidence: Decimal, auto_confirm: bool, note: str) -> ProcurementChainLink:
        link = self.db.query(ProcurementChainLink).filter_by(
            order_id=order_id, target_type=target_type, target_id=target_id
        ).first()
        if link is None:
            link = ProcurementChainLink(
                order_id=order_id, target_type=target_type, target_id=target_id,
                match_method="auto", confidence=confidence,
                confirmed=auto_confirm, note=note,
            )
            self.db.add(link)
        elif auto_confirm and link.match_method != "rejected":
            link.confirmed = True
        if auto_confirm and target_type == "inbound" and link.match_method != "rejected":
            auto_confirm_inbound_link(link)
        return link

    def _upsert_invoice_link(self, order_id: int, invoice_id: int,
                             confidence: Decimal, auto_confirm: bool, note: str) -> TaxInvoiceLink:
        link = self.db.query(TaxInvoiceLink).filter_by(
            target_type="alibaba1688_order", target_id=order_id, invoice_id=invoice_id
        ).first()
        if link is None:
            link = TaxInvoiceLink(
                invoice_id=invoice_id, target_type="alibaba1688_order", target_id=order_id,
                match_method="auto", confidence=confidence, confirmed=auto_confirm, note=note,
            )
            self.db.add(link)
        elif auto_confirm and link.match_method != "rejected":
            link.confirmed = True
        if auto_confirm and link.match_method != "rejected":
            invoice = self.db.get(TaxInvoice, invoice_id)
            if invoice is not None:
                mark_invoice_linked(invoice, note or "采购链自动关联")
        return link

    # ---------- 人工关联/确认 ----------

    def manual_link(self, order_id: int, target_type: str, target_id: int, note: str = "") -> ProcurementChainLink:
        self._validate_order_and_target(order_id, target_type, target_id)
        identity = {"order_id": order_id} if order_id > 0 else {"external_po_id": -order_id}
        link = self.db.query(ProcurementChainLink).filter_by(
            **identity, target_type=target_type, target_id=target_id
        ).first()
        if link is None:
            link = ProcurementChainLink(
                **identity, target_type=target_type, target_id=target_id,
                match_method="manual", note=note,
            )
            self.db.add(link)
        link.match_method = "manual"
        link.confidence = Decimal("1")
        link.note = note or "人工选择关联"
        link.confirmed = True
        self.db.commit()
        # 链路建链后：自动把已关联入库单的明细反填为 PO 的 SKU 分配"待确认"项
        if target_type == "inbound":
            try:
                seed_allocations_for_link(self.db, link)
            except Exception:  # 反填失败不应阻塞主链路
                pass
        return link

    def replace_link(self, link_id: int, target_id: int, note: str = "") -> ProcurementChainLink:
        """原子更换一条已有关联；若目标已有建议，则复用并确认该建议。"""
        link = self.db.get(ProcurementChainLink, link_id)
        if link is None:
            raise ValueError("关联不存在")
        self._validate_order_and_target(link.workbench_order_id, link.target_type, target_id)
        duplicate = self.db.query(ProcurementChainLink).filter(
            ProcurementChainLink.order_id == link.order_id,
            ProcurementChainLink.external_po_id == link.external_po_id,
            ProcurementChainLink.target_type == link.target_type,
            ProcurementChainLink.target_id == target_id,
            ProcurementChainLink.id != link.id,
        ).first()
        if link.target_type == "inbound":
            from app.services.consumable_service import _reverse_inbound_usage
            _reverse_inbound_usage(self.db, link.id)
            link.consumable_usage_decided = False
            link.consumable_usage_enabled = None
            # 释放旧入库单反填的 SKU 分配行（target_id 尚未变更，release 按旧单定位）
            try:
                from app.services.inbound_allocation_seed import release_inbound_seeds_for_link
                release_inbound_seeds_for_link(self.db, link, actor="system")
            except Exception:
                self.db.rollback()
        if duplicate is not None:
            if duplicate.target_type == "inbound":
                from app.services.consumable_service import _reverse_inbound_usage
                _reverse_inbound_usage(self.db, duplicate.id)
                duplicate.consumable_usage_decided = False
                duplicate.consumable_usage_enabled = None
            self.db.delete(link)
            link = duplicate
        else:
            link.target_id = target_id
        link.match_method = "manual"
        link.confidence = Decimal("1")
        link.note = note or "人工更换关联"
        link.confirmed = True
        self.db.commit()
        if link.target_type == "inbound":
            try:
                seed_allocations_for_link(self.db, link)
            except Exception:
                pass
        return link

    def _validate_order_and_target(self, order_id: int, target_type: str, target_id: int) -> None:
        if not any((o.id if o else -e.id) == order_id or (e and -e.id == order_id) for o, e in _source_pairs(self.db)):
            raise ValueError("采购订单不存在或已删除")
        if target_type == "inbound":
            target = self.db.get(JackyunGoodsDocument, target_id)
            if target is None or target.document_type != "inbound":
                raise ValueError("入库单不存在")
        elif target_type == "settlement":
            if self.db.get(JackyunPurchaseSettlement, target_id) is None:
                raise ValueError("结算单不存在")
        else:
            raise ValueError("不支持的关联类型")

    def manual_invoice_link(self, order_id: int, invoice_id: int, note: str = "") -> TaxInvoiceLink:
        source, external = next(((o, e) for o, e in _source_pairs(self.db)
                                 if (o.id if o else -e.id) == order_id), (None, None))
        if source is None and external is None:
            raise ValueError("采购订单不存在或已删除")
        invoice = _invoice_of(self.db, None, invoice_id)
        if invoice is None or invoice.direction != "input":
            raise ValueError("请选择有效的进项发票")
        target_type = "alibaba1688_order" if source else "external_purchase_order"
        target_id = source.id if source else external.id
        link = self.db.query(TaxInvoiceLink).filter_by(
            target_type=target_type, target_id=target_id, invoice_id=invoice_id
        ).first()
        if link is None:
            link = TaxInvoiceLink(
                invoice_id=invoice_id, target_type=target_type, target_id=target_id,
                match_method="manual", note=note,
            )
            self.db.add(link)
        link.match_method = "manual"
        link.confidence = Decimal("1")
        link.note = note or "人工选择关联"
        link.confirmed = True
        mark_invoice_linked(invoice, link.note)
        self.db.commit()
        return link

    def confirm(self, link_id: int) -> ProcurementChainLink:
        link = self.db.get(ProcurementChainLink, link_id)
        if link is None:
            raise ValueError("关联不存在")
        if link.target_type == "inbound" and not link.consumable_usage_decided:
            raise ValueError("确认入库关联前必须先确认是否添加耗材使用及数量")
        link.confirmed = True
        self.db.commit()
        # 确认预关联建议后：自动反填该入库单明细为 SKU 分配（此前确认入口不触发反填）
        if link.target_type == "inbound":
            try:
                from app.services.inbound_allocation_seed import seed_allocations_for_link
                seed_allocations_for_link(self.db, link)
            except Exception:  # 反填失败允许后续重试，不阻塞确认
                self.db.rollback()
        return link

    def confirm_invoice(self, link_id: int) -> TaxInvoiceLink:
        link = self.db.get(TaxInvoiceLink, link_id)
        if link is None:
            raise ValueError("关联不存在")
        link.confirmed = True
        invoice = self.db.get(TaxInvoice, link.invoice_id)
        if invoice is not None:
            mark_invoice_linked(invoice, link.note or "采购链自动关联")
        self.db.commit()
        return link

    def remove_link(self, link_id: int) -> None:
        link = self.db.get(ProcurementChainLink, link_id)
        if link is not None:
            if link.target_type == "inbound":
                from app.services.consumable_service import _reverse_inbound_usage
                _reverse_inbound_usage(self.db, link.id)
                link.consumable_usage_decided = False
                link.consumable_usage_enabled = None
                # 释放该入库单反填的 SKU 分配行，避免残留行占用明细行、虚增订单金额
                try:
                    from app.services.inbound_allocation_seed import release_inbound_seeds_for_link
                    release_inbound_seeds_for_link(self.db, link, actor="system")
                except Exception:
                    self.db.rollback()
            # 保留拒绝/解除记录，避免下次“生成匹配建议”又把同一组合推回来。
            link.confirmed = False
            link.match_method = "rejected"
            link.confidence = None
            link.note = "人工拒绝或解除关联"
            self.db.commit()

    def remove_invoice_link(self, link_id: int) -> None:
        link = self.db.get(TaxInvoiceLink, link_id)
        if link is not None:
            link.confirmed = False
            link.match_method = "rejected"
            link.confidence = None
            link.note = "人工拒绝或解除关联"
            self.db.commit()

    def set_invoice_verified(self, invoice_id: int, verified: bool, verified_month: str = "") -> TaxInvoice:
        invoice = self.db.get(TaxInvoice, invoice_id)
        if invoice is None:
            raise ValueError("发票不存在")
        if verified and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", verified_month):
            raise ValueError("请填写实际认证所属月份，格式 YYYY-MM")
        invoice.verified = verified
        invoice.verified_month = verified_month if verified else ""
        invoice.verified_at = datetime.now() if verified else None
        self.db.commit()
        return invoice


def auto_confirm_pending_links(db: Session, actor: str = "system") -> dict:
    """把当前可验证的采购链关联一次性确认。

    入库链没有耗材来源信息时只确认“本次不使用耗材”，绝不自动扣库存；已明确
    使用耗材的记录原样保留。缺少来源或目标单据的孤儿关系跳过并返回数量，方便验收。
    """
    chain_links = db.query(ProcurementChainLink).filter(
        ProcurementChainLink.match_method != "rejected"
    ).all()
    invoice_links = db.query(TaxInvoiceLink).filter(
        TaxInvoiceLink.target_type.in_(
            ("alibaba1688_order", "external_purchase_order")
        ),
        TaxInvoiceLink.match_method != "rejected",
    ).all()

    confirmed_chain = 0
    resolved_inbound_usage = 0
    confirmed_invoices = 0
    skipped_orphans = 0
    inbound_links: list[ProcurementChainLink] = []

    for link in chain_links:
        if link.order_id is not None:
            source = db.get(Alibaba1688Order, link.order_id)
            if source is None or source.row_status == "deleted":
                skipped_orphans += 1
                continue
            if source.import_id:
                source_import = db.get(Alibaba1688FileImport, source.import_id)
                if source_import is not None and source_import.lifecycle != "active":
                    skipped_orphans += 1
                    continue
        elif link.external_po_id is not None:
            if db.get(ExternalPurchaseOrder, link.external_po_id) is None:
                skipped_orphans += 1
                continue
        else:
            skipped_orphans += 1
            continue

        if link.target_type == "inbound":
            target = db.get(JackyunGoodsDocument, link.target_id)
            if target is None or target.document_type != "inbound":
                skipped_orphans += 1
                continue
            was_confirmed = bool(link.confirmed)
            needs_usage_decision = (
                not link.consumable_usage_decided
                or link.consumable_usage_enabled is None
            )
            if not was_confirmed or needs_usage_decision:
                if auto_confirm_inbound_link(link):
                    if not was_confirmed:
                        confirmed_chain += 1
                    if needs_usage_decision:
                        resolved_inbound_usage += 1
            inbound_links.append(link)
            continue

        if link.target_type == "settlement":
            if db.get(JackyunPurchaseSettlement, link.target_id) is None:
                skipped_orphans += 1
                continue
            if not link.confirmed:
                link.confirmed = True
                link.note = f"{link.note}；自动确认" if link.note else "自动确认"
                confirmed_chain += 1
            continue

        skipped_orphans += 1

    for link in invoice_links:
        invoice = db.get(TaxInvoice, link.invoice_id)
        if invoice is None:
            skipped_orphans += 1
            continue
        if link.target_type == "alibaba1688_order":
            source = db.get(Alibaba1688Order, link.target_id)
            if source is None or source.row_status == "deleted":
                skipped_orphans += 1
                continue
            if source.import_id:
                source_import = db.get(Alibaba1688FileImport, source.import_id)
                if source_import is not None and source_import.lifecycle != "active":
                    skipped_orphans += 1
                    continue
        elif db.get(ExternalPurchaseOrder, link.target_id) is None:
            skipped_orphans += 1
            continue
        if not link.confirmed:
            link.confirmed = True
            link.note = f"{link.note}；自动确认" if link.note else "自动确认"
            confirmed_invoices += 1
        mark_invoice_linked(invoice, link.note or "采购链自动关联")

    db.commit()
    allocation = {"seeded": 0, "skipped": 0, "touched_po_ids": []}
    seed_error = ""
    if inbound_links:
        try:
            from app.services.inbound_allocation_seed import seed_for_link_batch

            allocation = seed_for_link_batch(db, inbound_links)
        except Exception as exc:  # 链路已确认，SKU 反填允许后续重试
            db.rollback()
            seed_error = str(exc)
    audit(
        db,
        actor,
        "purchase.procurement_chain.auto_confirm",
        "procurement_chain_links",
        0,
        {
            "confirmedChain": confirmed_chain,
            "resolvedInboundUsage": resolved_inbound_usage,
            "confirmedInvoices": confirmed_invoices,
            "skippedOrphans": skipped_orphans,
            "allocSeeded": allocation.get("seeded", 0),
            "seedError": seed_error,
        },
    )
    return {
        "confirmedChain": confirmed_chain,
        "resolvedInboundUsage": resolved_inbound_usage,
        "confirmedInvoices": confirmed_invoices,
        "skippedOrphans": skipped_orphans,
        "allocSeeded": int(allocation.get("seeded", 0)),
        "allocSkipped": int(allocation.get("skipped", 0)),
        "seedError": seed_error,
    }


def purge_voided_invoice_links(db: Session, actor: str = "system", dry_run: bool = True) -> dict:
    """清理建立在作废发票上的采购关联（status=red 或红字负数票）。

    早期版本没有过滤作废票，历史库里可能残留这类关联：已红冲的蓝字票被计进
    采购金额，会造成票面金额虚高。默认 dry_run，确认清单后再实际删除。
    """
    links = (
        db.query(TaxInvoiceLink)
        .join(TaxInvoice, TaxInvoice.id == TaxInvoiceLink.invoice_id)
        .filter(
            TaxInvoiceLink.target_type == "alibaba1688_order",
            or_(TaxInvoice.status == INVOICE_STATUS_RED, TaxInvoice.total_amount <= 0),
        )
        .all()
    )
    items: list[dict] = []
    for link in links:
        inv = db.get(TaxInvoice, link.invoice_id)
        items.append({
            "linkId": link.id,
            "orderId": link.target_id,
            "invoiceId": link.invoice_id,
            "invoiceNo": _invoice_no(inv) if inv is not None else "",
            "amount": float(inv.total_amount) if inv is not None and inv.total_amount is not None else None,
            "status": (inv.status if inv is not None else "") or "",
            "issueDate": inv.issue_date.isoformat() if inv is not None and inv.issue_date else None,
        })
    if dry_run:
        return {"ok": True, "dryRun": True, "matched": len(items), "removed": 0, "items": items}
    for link in links:
        inv = db.get(TaxInvoice, link.invoice_id)
        db.delete(link)
        if inv is not None:
            inv.match_status = "unmatched"
            inv.match_note = f"{(inv.match_note or '')}；已解除作废票关联".strip("；")
        audit(
            db, actor, "procurement.invoice.purge_voided", "tax_invoice_link", str(link.id),
            {"invoiceId": link.invoice_id, "orderId": link.target_id},
        )
    db.commit()
    return {"ok": True, "dryRun": False, "matched": len(items), "removed": len(items), "items": items}


def run_full_procurement_automation(db: Session, actor: str = "system") -> dict:
    """运行一次完整采购自动化：入库预关联、订单匹配、待确认关系清扫。"""
    result: dict = {"ok": True, "errors": []}
    try:
        from app.services.procurement_prelink_service import prelink_inbound

        result["prelink"] = prelink_inbound(db, actor=actor, auto=True)
    except Exception as exc:
        db.rollback()
        result["ok"] = False
        result["errors"].append(f"入库预关联：{exc}")
    try:
        result["match"] = ProcurementChainMatcher(db).run_match(auto_confirm=True)
    except Exception as exc:
        db.rollback()
        result["ok"] = False
        result["errors"].append(f"订单匹配：{exc}")
    try:
        result["autoConfirm"] = auto_confirm_pending_links(db, actor=actor)
    except Exception as exc:
        db.rollback()
        result["ok"] = False
        result["errors"].append(f"关联确认：{exc}")
    try:
        # 采购单 Phase-1 自动匹配：吉客云采购单已导入后，自动关联已确认订单并推进状态。
        from app.services.purchase_service import auto_link_purchase_orders
        result["purchaseOrderLink"] = auto_link_purchase_orders(db, actor=actor)
    except Exception as exc:
        db.rollback()
        result["ok"] = False
        result["errors"].append(f"采购单自动关联：{exc}")
    return result


# ---------- 查询聚合 ----------

def _invoice_no(inv: TaxInvoice) -> str:
    return f"{inv.invoice_code or ''}{inv.invoice_number or ''}"


def _serialize_invoice(inv: TaxInvoice) -> dict:
    return {
        "invoiceId": inv.id,
        "invoiceNo": _invoice_no(inv),
        "amount": float(inv.total_amount) if inv.total_amount is not None else None,
        "issueDate": inv.issue_date.isoformat() if inv.issue_date else None,
        "verified": inv.verified,
        "verifiedMonth": inv.verified_month or "",
    }


def _chain_target(db: Session, target_type: str, target_id: int):
    if target_type == "inbound":
        return db.get(JackyunGoodsDocument, target_id)
    if target_type == "settlement":
        return db.get(JackyunPurchaseSettlement, target_id)
    return None


def _chain_target_fields(target_type: str, target) -> dict:
    if target_type == "inbound" and isinstance(target, JackyunGoodsDocument):
        return {
            "targetNo": target.goodsdoc_no,
            "targetAmount": _num(target.total_amount),
            "targetDate": target.document_at.isoformat() if target.document_at else None,
            "targetSupplier": target.supplier_name or target.company_name or "",
        }
    if target_type == "settlement" and isinstance(target, JackyunPurchaseSettlement):
        amount = target.settlement_amount if target.settlement_amount is not None else target.total_amount
        return {
            "targetNo": target.settlement_no,
            "targetAmount": _num(amount),
            "targetDate": target.settlement_date.isoformat() if target.settlement_date else None,
            "targetSupplier": target.supplier_name or target.company_name or "",
        }
    return {"targetNo": "", "targetAmount": None, "targetDate": None, "targetSupplier": ""}


def list_link_candidates(
    db: Session,
    order_id: int,
    target_type: str,
    q: str = "",
    limit: int = 100,
) -> dict:
    """列出可由用户手工选择的单据，并把匹配线索作为排序说明展示。"""
    source, external = next(((o, e) for o, e in _source_pairs(db) if (o.id if o else -e.id) == order_id), (None, None))
    if source is None and external is None:
        raise ValueError("采购订单不存在或已删除")
    if target_type == "inbound":
        targets = db.query(JackyunGoodsDocument).filter_by(document_type="inbound").all()
    elif target_type == "settlement":
        targets = db.query(JackyunPurchaseSettlement).all()
    else:
        raise ValueError("target_type 只能是 inbound/settlement")

    all_links = db.query(ProcurementChainLink).filter_by(target_type=target_type).all()
    current_ids = {order_id} | ({-external.id} if external else set())
    current_links = {link.target_id: link for link in all_links if link.workbench_order_id in current_ids}
    order_nos = {o.id: o.external_order_id for o, _ in _source_pairs(db) if o}
    order_nos.update({-e.id: e.external_order_id for _, e in _source_pairs(db) if e})
    linked_nos: dict[int, list[str]] = {}
    for link in all_links:
        if link.confirmed and link.workbench_order_id in order_nos:
            linked_nos.setdefault(link.target_id, []).append(order_nos[link.workbench_order_id])

    query = q.strip().lower()
    order_supplier = normalize_name(source.seller_company_name if source else external.supplier_name)
    order_amount = parse_decimal(source.actual_payment if source else external.paid_amount)
    ordered_at = source.order_time if source else external.ordered_at
    order_no = source.external_order_id if source else external.external_order_id

    # SKU 重合预计算：订单必到 SKU（采购分配） vs 各入库单已匹配 SKU
    required_skus: set[int] = set()
    if target_type == "inbound":
        if external is not None:
            alloc_rows = db.query(PurchaseAllocationItem.sku_id).filter(
                PurchaseAllocationItem.po_id == external.id,
                PurchaseAllocationItem.sku_id.isnot(None),
            ).all()
            required_skus = {int(sid) for (sid,) in alloc_rows if sid is not None}
        if required_skus:
            item_rows = (
                db.query(JackyunGoodsDocumentItem.document_id, JackyunGoodsDocumentItem.matched_sku_id)
                .filter(JackyunGoodsDocumentItem.matched_sku_id.in_(required_skus))
                .all()
            )
            doc_sku: dict[int, set[int]] = {}
            for doc_id, sku_id in item_rows:
                doc_sku.setdefault(doc_id, set()).add(int(sku_id))

    rows: list[dict] = []
    for target in targets:
        fields = _chain_target_fields(target_type, target)
        target_no = str(fields["targetNo"] or "")
        supplier = str(fields["targetSupplier"] or "")
        haystack = f"{target_no} {supplier} {getattr(target, 'warehouse_name', '')}".lower()
        if query and query not in haystack:
            continue

        target_amount = parse_decimal(fields["targetAmount"])
        target_date = target.document_at if target_type == "inbound" else target.settlement_date
        days = _days_apart(ordered_at, target_date)
        supplier_match = bool(order_supplier and normalize_name(supplier) == order_supplier)
        amount_match = bool(order_amount is not None and target_amount is not None and amount_close(order_amount, target_amount))
        explicit_match = bool(
            target_type == "inbound" and order_no in _document_order_refs(getattr(target, "raw", {}))
        )
        match_skus = doc_sku.get(target.id, set()) if target_type == "inbound" and required_skus else set()
        overlap_ratio = (len(match_skus) / len(required_skus)) if required_skus else 0.0

        if explicit_match:
            score, reason = 1.0, "单据明确记录本订单号"
        elif target_type == "inbound" and required_skus:
            if match_skus:
                # 多因子打分：SKU 重合为主导，供应商与时间辅助，金额仅弱加分
                if target_date is not None and ordered_at is not None:
                    delta_days = (target_date.replace(tzinfo=None) - ordered_at.replace(tzinfo=None)).total_seconds() / 86400
                else:
                    delta_days = None
                in_window = delta_days is not None and -7 <= delta_days <= 120
                if in_window:
                    time_score = min(1.0, 10 / max((days or 0), 0.1)) if (days or 0) > 0 else 1.0
                    time_part = "时间相近" if time_score >= 0.5 else f"时间相距 {days:.0f} 天"
                else:
                    time_score, time_part = 0.0, ("缺时间" if delta_days is None else f"时间相距 {days:.0f} 天超窗")
                if not in_window:
                    score, reason = 0.0, f"SKU 重合 {len(match_skus)}/{len(required_skus)} 但{time_part}，需人工核对"
                else:
                    score = 0.55 * overlap_ratio + 0.20 * (1 if supplier_match else 0) + 0.25 * time_score
                    if amount_match:
                        score = min(1.0, score + 0.03)
                    score = round(score, 4)
                    reason = (f"SKU 重合 {len(match_skus)}/{len(required_skus)}"
                              + ("、供应商一致" if supplier_match else "、供应商待核")
                              + f"、{time_part}"
                              + ("、金额接近" if amount_match else ""))
            elif supplier_match and amount_match and days is not None and days <= MATCH_WINDOW_DAYS:
                score, reason = 0.6, f"供应商金额一致、时间相距 {days:.0f} 天，但单据无本单 SKU 匹配（或档案未匹配），建议人工核对"
            elif supplier_match:
                score, reason = 0.4, "供应商一致，无 SKU 重合，需人工核对"
            else:
                score, reason = 0.0, "无本单 SKU 重合，可人工选择"
        elif supplier_match and amount_match and days is not None and days <= MATCH_WINDOW_DAYS:
            score, reason = 0.9, f"供应商、金额一致，时间相距 {days:.0f} 天"
        elif supplier_match and amount_match:
            score, reason = 0.75, "供应商、金额一致，时间距离较远"
        elif supplier_match:
            score, reason = 0.5, "供应商一致，金额需人工核对"
        elif amount_match:
            score, reason = 0.35, "金额接近，供应商需人工核对"
        else:
            score, reason = 0.0, "无自动匹配依据，可人工选择"

        current = current_links.get(target.id)
        rows.append({
            "targetId": target.id,
            "targetType": target_type,
            **fields,
            "warehouseName": getattr(target, "warehouse_name", "") or "",
            "score": score,
            "reason": reason,
            "requiredSkuCount": len(required_skus) if target_type == "inbound" else 0,
            "matchedSkuCount": len(match_skus),
            "skuOverlapRatio": round(overlap_ratio, 4) if target_type == "inbound" else None,
            "linkId": current.id if current is not None else None,
            "currentlyLinked": bool(current and current.confirmed),
            "pendingSuggestion": bool(current and not current.confirmed and current.match_method != "rejected"),
            "previouslyRejected": bool(current and current.match_method == "rejected"),
            "linkedOrderNos": sorted(set(linked_nos.get(target.id, []))),
            "_dayDistance": days if days is not None else float("inf"),
        })
    # 批量取每个入库单的明细行（货品名/规格/数量/单价/已匹配 SKU），方便候选 UI 直接展示
    details_by_doc: dict[int, list[dict]] = {}
    if rows:
        inbound_ids = [r["targetId"] for r in rows if r["targetType"] == "inbound"]
        if inbound_ids:
            item_rows = (
                db.query(JackyunGoodsDocumentItem)
                .filter(JackyunGoodsDocumentItem.document_id.in_(inbound_ids))
                .order_by(
                    JackyunGoodsDocumentItem.document_id,
                    JackyunGoodsDocumentItem.line_no,
                )
                .all()
            )
            for it in item_rows:
                details_by_doc.setdefault(it.document_id, []).append({
                    "goodsName": it.goods_name or "",
                    "spec": it.spec or "",
                    "quantity": _num(it.quantity),
                    "applyQuantity": _num(it.apply_quantity),
                    "remainQuantity": _num(it.remain_quantity),
                    "barcode": it.sku_barcode or "",
                    "unitName": it.unit_name or "",
                    "unitPriceTax": _num(it.unit_price_tax),
                    "amountTax": _num(it.amount_tax),
                    "skuId": it.matched_sku_id,
                    "matchStatus": it.match_status or "",
                })
        for r in rows:
            r["details"] = details_by_doc.get(r["targetId"], []) if r["targetType"] == "inbound" else []
    rows.sort(key=lambda row: (
        not row["currentlyLinked"],
        not row["pendingSuggestion"],
        -row["score"],
        row["_dayDistance"],
        row["targetNo"],
    ))
    for row in rows:
        row.pop("_dayDistance", None)
    return {"total": len(rows), "items": rows[:limit]}


def _source_pairs(db: Session) -> list[tuple[Alibaba1688Order | None, ExternalPurchaseOrder | None]]:
    """按 1688 订单号合并文件副本与采购工作流副本，保证界面一行一单。

    ``alibaba1688_orders`` 保存官方导出原件的标准副本，
    ``external_purchase_orders`` 保存 SKU 分配、状态和吉客云采购单关联。
    两张表是不同职责，不能在查询层重复展示成两笔订单。

    用户在导入明细里删除的行（row_status=deleted）整单退出链路：
    文件副本不出现，同单号的工作流副本也不作为残留行出现（该订单被明确判定为不要）。
    同理，若订单仍有文件副本但该批次已删除或尚未确认，不能让工作流副本绕过
    导入生命周期单独露出；没有任何文件副本的手工/OAuth 订单仍会保留。
    """
    file_orders = (
        filter_active_rows(
            filter_active_import(
                db.query(Alibaba1688Order), Alibaba1688Order, Alibaba1688FileImport, Alibaba1688Order.import_id
            ),
            Alibaba1688Order,
        )
        .order_by(Alibaba1688Order.id.desc())
        .all()
    )
    # 被用户删除的订单号：同单号的工作流副本不再补位出现。
    removed_nos = {
        no for (no,) in db.query(Alibaba1688Order.external_order_id).filter(
            Alibaba1688Order.row_status == "deleted"
        ).all()
    }
    # 存在文件来源但并非 active 的订单，等待该批次确认或重新导入后才可进入链路。
    # 这避免“已删除批次 + 残留工作流”制造一条看似可处理、实则没有原始依据的订单。
    source_nos = {
        no for (no,) in db.query(Alibaba1688Order.external_order_id).all()
    }
    active_source_nos = {order.external_order_id for order in file_orders}
    workflow_by_no = {
        po.external_order_id: po
        for po in db.query(ExternalPurchaseOrder).order_by(ExternalPurchaseOrder.id.desc()).all()
        if po.external_order_id not in removed_nos
        and (po.external_order_id in active_source_nos or po.external_order_id not in source_nos)
    }
    pairs: list[tuple[Alibaba1688Order | None, ExternalPurchaseOrder | None]] = []
    for order in file_orders:
        pairs.append((order, workflow_by_no.pop(order.external_order_id, None)))
    # 手工登记或 OAuth 同步但尚未有导出文件的订单也要出现在同一张链路表中。
    pairs.extend((None, po) for po in workflow_by_no.values())
    return pairs


# 分配行 note 里记录的来源入库单，格式见 app/services/inbound_allocation_seed.py
# 的 note=f"由入库单 #{document_id} 明细自动反填"。这里反解出 document_id，
# 让前端能把 SKU 分配按入库单分组展示，而不必依赖脆弱的文本匹配。
_INBOUND_DOC_NOTE_RE = re.compile(r"由入库单\s*#(\d+)")


def parse_inbound_doc_id(note: str | None) -> int | None:
    """反解分配行的来源入库单 ID；人工新增行没有该标记，返回 None。"""
    match = _INBOUND_DOC_NOTE_RE.search(note or "")
    return int(match.group(1)) if match else None


def _serialize_allocation(row: PurchaseAllocationItem) -> dict:
    return {
        "id": row.id,
        "skuId": row.sku_id,
        "skuCode": row.sku_code or "",
        "goodsName": row.goods_name or "",
        "quantity": _num(row.quantity),
        "unitPrice": _num(row.unit_price),
        "amount": _num(row.amount),
        "note": row.note or "",
        # 来源入库单：自动反填行有值，人工新增行为 null（前端归入「手工补录」区）
        "inboundDocumentId": parse_inbound_doc_id(row.note),
    }


def _serialize_jackyun_purchase(row: JackyunPurchaseOrder) -> dict:
    return {
        "id": row.id,
        "purchNo": row.purch_no or row.jackyun_purch_id,
        "supplierName": row.supplier_name or "",
        "amount": _num(row.amount),
        "status": row.status or "",
    }


def _serialize_expense(row: PurchaseExtraExpense) -> dict:
    return {
        "id": row.id,
        "expenseType": row.expense_type or "other",
        "amount": _num(row.amount),
        "note": row.note or "",
    }


class ChainPrefetch:
    """链路聚合的批量预加载缓存。

    列表页与漏斗统计要遍历全部订单，逐行查库会产生上千次单行查询（实测 100 单 ≈ 550ms）。
    这里一次性把关联表全量载入内存并按外键分组，把复杂度压到「表数量」而非「订单数 × 每张表」。
    单条订单详情仍走点查，不预加载。
    """

    def __init__(self, db: Session):
        self.external_by_no = {
            po.external_order_id: po for po in db.query(ExternalPurchaseOrder).all()
        }
        self.docs = {
            d.id: d for d in db.query(JackyunGoodsDocument).filter_by(document_type="inbound").all()
        }
        self.docs_by_no = {d.goodsdoc_no: d for d in self.docs.values()}
        self.settlements = {s.id: s for s in db.query(JackyunPurchaseSettlement).all()}
        self.invoices = {
            i.id: i for i in filter_visible_invoices(db.query(TaxInvoice)).all()
        }
        self.jackyun_pos = {p.id: p for p in db.query(JackyunPurchaseOrder).all()}

        self.chain_links: dict[int, list[ProcurementChainLink]] = {}
        for link in db.query(ProcurementChainLink).all():
            self.chain_links.setdefault(link.workbench_order_id, []).append(link)

        self.invoice_links: dict[tuple[str, int], list[TaxInvoiceLink]] = {}
        for link in db.query(TaxInvoiceLink).filter(
            TaxInvoiceLink.target_type.in_(("alibaba1688_order", "external_purchase_order"))
        ).all():
            self.invoice_links.setdefault((link.target_type, link.target_id), []).append(link)

        self.inbound_links: dict[int, list[InboundLink]] = {}
        for link in db.query(InboundLink).all():
            self.inbound_links.setdefault(link.po_id, []).append(link)

        self.allocations: dict[int, list[PurchaseAllocationItem]] = {}
        for row in db.query(PurchaseAllocationItem).all():
            self.allocations.setdefault(row.po_id, []).append(row)

        self.po_links: dict[int, list[JackyunPurchaseOrderLink]] = {}
        for link in db.query(JackyunPurchaseOrderLink).all():
            self.po_links.setdefault(link.po_id, []).append(link)

        self.expenses: dict[int, list[PurchaseExtraExpense]] = {}
        for row in db.query(PurchaseExtraExpense).all():
            self.expenses.setdefault(row.po_id, []).append(row)


# ---------- 取值助手：有预加载走内存，无预加载退回点查 ----------

def _external_of(db: Session, pf: ChainPrefetch | None, order_no: str) -> ExternalPurchaseOrder | None:
    if pf is not None:
        return pf.external_by_no.get(order_no)
    return db.query(ExternalPurchaseOrder).filter_by(external_order_id=order_no).first()


def _chain_links_of(db: Session, pf: ChainPrefetch | None, order_id: int,
                    target_type: str | None = None, confirmed: bool | None = None) -> list[ProcurementChainLink]:
    if pf is not None:
        rows = pf.chain_links.get(order_id, [])
    else:
        identity = {"order_id": order_id} if order_id > 0 else {"external_po_id": -order_id}
        rows = db.query(ProcurementChainLink).filter_by(**identity).all()
    if target_type is not None:
        rows = [r for r in rows if r.target_type == target_type]
    rows = [r for r in rows if r.match_method != "rejected"]
    if confirmed is not None:
        rows = [r for r in rows if r.confirmed == confirmed]
    return rows


def _invoice_links_of(db: Session, pf: ChainPrefetch | None, target_type: str, target_id: int,
                      confirmed: bool | None = None) -> list[TaxInvoiceLink]:
    if pf is not None:
        rows = pf.invoice_links.get((target_type, target_id), [])
    else:
        rows = db.query(TaxInvoiceLink).filter_by(target_type=target_type, target_id=target_id).all()
    rows = [r for r in rows if r.match_method != "rejected"]
    if confirmed is not None:
        rows = [r for r in rows if r.confirmed == confirmed]
    return rows


def _doc_of(db: Session, pf: ChainPrefetch | None, doc_id: int) -> JackyunGoodsDocument | None:
    if pf is not None:
        return pf.docs.get(doc_id)
    return db.get(JackyunGoodsDocument, doc_id)


def _inbound_item_amounts(db: Session, document_id: int) -> tuple[Decimal | None, int]:
    """入库单明细合计金额与行数（用于单据头与明细对账）。"""
    from app.models.jackyun import JackyunGoodsDocumentItem

    rows = db.query(JackyunGoodsDocumentItem.amount_tax).filter(
        JackyunGoodsDocumentItem.document_id == document_id
    ).all()
    if not rows:
        return None, 0
    total = Decimal(0)
    for (value,) in rows:
        if value is not None:
            total += value
    return total, len(rows)


def _settlement_of(db: Session, pf: ChainPrefetch | None, sid: int) -> JackyunPurchaseSettlement | None:
    if pf is not None:
        return pf.settlements.get(sid)
    return db.get(JackyunPurchaseSettlement, sid)


def _invoice_of(db: Session, pf: ChainPrefetch | None, invoice_id: int) -> TaxInvoice | None:
    if pf is not None:
        return pf.invoices.get(invoice_id)
    return db.get(TaxInvoice, invoice_id)


def list_chain(db: Session, limit: int = 100, offset: int = 0) -> dict:
    """返回采购全链路列表：每个 1688 订单号只占一行。"""
    pairs = _source_pairs(db)
    total = len(pairs)
    pf = ChainPrefetch(db)
    rows: list[dict] = []
    for order, external in pairs[offset:offset + limit]:
        rows.append(_order_row(db, order, external, pf=pf))
    return {"total": total, "items": rows}


def _extract_1688_items(order: Alibaba1688Order | None) -> list[dict]:
    """从 1688 直采原始报文提取货品明细（含「编号」），供「确认采购内容」环节展示。

    浏览器直采把完整报文存在 ``raw_payload["_raw"]``，其中 ``orderEntries`` 即货品行，
    内含 productNumber（编号/货号）、productName、quantity、actualUnitPrice、
    entryStatusLabel（如「已收货」）。此前没有任何代码解析它，导致未关联入库单的订单
    在「确认采购内容」环节看不到编号（2026-09-07 反馈：订单 5092801200511821020）。

    金额口径：1688 报文金额单位为「分」，统一换算为元。
    行金额优先用 数量 × actualUnitPrice 还原——entry.amount 只反映改价前的行小计，
    改价差额落在 adjustFee（模型未落列），直接用 amount 会与实付对不上。
    """
    if order is None:
        return []

    def _loads(value):
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return None
        return value

    payload = _loads(order.raw_payload)
    if not isinstance(payload, dict):
        return []
    raw = _loads(payload.get("_raw"))
    if not isinstance(raw, dict):
        raw = payload if "orderEntries" in payload else {}
    entries = raw.get("orderEntries") or []
    if not isinstance(entries, list):
        return []

    def _yuan(value):
        try:
            return round(float(value) / 100.0, 2)
        except Exception:
            return None

    def _float(value):
        try:
            return float(value) if value not in (None, "") else None
        except Exception:
            return None

    items: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        qty_raw = entry.get("quantity")
        qty = qty_raw.get("realAmount") or qty_raw.get("calAmount") if isinstance(qty_raw, dict) else qty_raw
        qty = _float(qty)
        unit_price = _yuan(entry.get("actualUnitPrice") or entry.get("price"))
        amount = _yuan(entry.get("amount"))
        if unit_price is not None and qty:
            amount = round(unit_price * qty, 2)
        spec = ""
        for spec_item in ((entry.get("specInfo") or {}).get("specItems") or []):
            if isinstance(spec_item, dict) and spec_item.get("specValue"):
                spec = str(spec_item["specValue"])
                break
        extension = entry.get("entryExtension") or {}
        items.append({
            "productNumber": str(entry.get("productNumber") or "").strip(),
            "productName": str(entry.get("productName") or "").strip(),
            "skuId": str(entry.get("skuId") or "").strip(),
            "spec": spec,
            "quantity": qty,
            "unitPrice": unit_price,
            "amount": amount,
            "statusLabel": str(entry.get("entryStatusLabel") or "").strip(),
            "receivedQuantity": _float(extension.get("receivedQuantity")),
        })
    return items


def _consumable_of(
    db: Session, order: Alibaba1688Order | None, order_no: str | None = None
) -> dict | None:
    """取该订单关联的耗材采购 + 收货（耗材走本平台，不生成吉客云单据）。

    耗材收货是独立于吉客云入库单的一条线：收货即入库，且明细自带编号。
    此前工作台完全不读它，导致已收货的耗材单仍卡在「待确认采购内容」
    （2026-09-07 用户反馈：订单 5092801200511821020 已收齐却仍待确认）。

    匹配口径（与耗材面板 list_purchases 一致）：
    ① 1688 单按 source_order_id；② reference_no == 订单号。
    工作流独有单（手工登记，无 1688 原件）source_order_id=NULL，**只能靠单号匹配**
    ——只查 source_order_id 会让这类单收货后永远不流转（2026-09-07 用户反馈
    订单 4774467638483821020 收货保存成功但状态不动）。
    多张非 cancelled HC 单时聚合：received = 全部收齐。
    """
    if order is None and not order_no:
        return None
    from app.models.consumable_purchase import (
        ConsumablePurchase, ConsumablePurchaseItem, ConsumableReceipt,
    )

    conds = []
    if order is not None:
        conds.append(ConsumablePurchase.source_order_id == order.id)
    if order_no:
        conds.append(ConsumablePurchase.reference_no == order_no)
    purchases = (
        db.query(ConsumablePurchase)
        .filter(or_(*conds))
        .order_by(ConsumablePurchase.id)
        .all()
    )
    active = [p for p in purchases if (p.status or "") != "cancelled"]
    if not active:
        return None

    items: list = []
    receipts: list = []
    fully_received = True
    any_received = False
    for purchase in active:
        p_items = (
            db.query(ConsumablePurchaseItem).filter_by(purchase_id=purchase.id).all()
        )
        items.extend(p_items)
        p_receipts = (
            db.query(ConsumableReceipt)
            .filter_by(purchase_id=purchase.id)
            .order_by(ConsumableReceipt.id)
            .all()
        )
        receipts.extend(p_receipts)
        if not p_items:
            fully_received = False
        for it in p_items:
            got = Decimal(str(it.received_qty or 0))
            want = Decimal(str(it.quantity or 0))
            if got > 0:
                any_received = True
            if got < want:
                fully_received = False
    last = receipts[-1] if receipts else None
    status = "received" if fully_received else ("partial" if any_received else "ordered")
    return {
        "purchaseNo": "、".join(str(p.number) for p in active),
        "status": status,
        "fullyReceived": fully_received,
        "received": fully_received,
        "receiptNo": last.number if last else "",
        "receivedOn": last.received_on.isoformat() if (last is not None and last.received_on) else None,
        "location": (last.location if last else "") or "",
        "items": [
            {
                "code": it.code,
                "name": it.name,
                "unit": it.unit,
                "quantity": _num(it.quantity),
                "receivedQty": _num(it.received_qty),
                "unitCost": _num(it.unit_cost),
            }
            for it in items
        ],
    }


def _order_row(
    db: Session,
    order: Alibaba1688Order | None,
    external: ExternalPurchaseOrder | None = None,
    pf: ChainPrefetch | None = None,
) -> dict:
    """聚合两张 1688 表、采购分配、吉客云单据和税务清单为同一行。

    pf 传入时走预加载缓存（列表/统计场景），为空时走点查（单条详情场景）。
    """
    if external is None and order is not None:
        external = _external_of(db, pf, order.external_order_id)

    file_order_id = order.id if order is not None else None
    order_no = (order.external_order_id if order is not None else external.external_order_id)

    chain_inbound = []
    settlements = []
    pending = 0
    if file_order_id is not None:
        chain_inbound = _chain_links_of(db, pf, file_order_id, target_type="inbound", confirmed=True)
        settlements = _chain_links_of(db, pf, file_order_id, target_type="settlement", confirmed=True)
        pending += len(_chain_links_of(db, pf, file_order_id, confirmed=False))
    if external is not None:
        chain_inbound += _chain_links_of(db, pf, -external.id, target_type="inbound", confirmed=True)
        settlements += _chain_links_of(db, pf, -external.id, target_type="settlement", confirmed=True)
        pending += len(_chain_links_of(db, pf, -external.id, confirmed=False))

    invoice_links = []
    if file_order_id is not None:
        invoice_links.extend(_invoice_links_of(db, pf, "alibaba1688_order", file_order_id, confirmed=True))
        pending += len(_invoice_links_of(db, pf, "alibaba1688_order", file_order_id, confirmed=False))
    if external is not None:
        invoice_links.extend(_invoice_links_of(db, pf, "external_purchase_order", external.id, confirmed=True))
        pending += len(_invoice_links_of(db, pf, "external_purchase_order", external.id, confirmed=False))

    # 每个环节返回全部已确认关联明细（不折叠），缺失为空数组
    inbound_info: list[dict] = []
    seen_inbound: set[str] = set()
    for link in chain_inbound:
        doc = _doc_of(db, pf, link.target_id)
        if doc is None:
            continue
        if doc.goodsdoc_no in seen_inbound:
            continue
        seen_inbound.add(doc.goodsdoc_no)
        from app.services.consumable_service import inbound_usage_rows
        usage_items = inbound_usage_rows(db, link.id)
        item_sum, item_count = _inbound_item_amounts(db, doc.id)
        inbound_info.append({
            "linkId": link.id,
            "documentId": doc.id,
            "targetId": doc.id,
            "goodsdocNo": doc.goodsdoc_no,
            "date": doc.document_at.isoformat() if doc.document_at else None,
            "warehouseName": doc.warehouse_name or "",
            "supplier": doc.supplier_name or doc.company_name or "",
            "amount": _num(doc.total_amount),
            "itemAmount": _num(item_sum),
            "itemCount": item_count,
            "matchMethod": link.match_method or "",
            "confidence": _num(link.confidence),
            "note": link.note or "",
            "consumableUsageDecided": bool(link.consumable_usage_decided),
            "consumableUsageEnabled": link.consumable_usage_enabled,
            "consumableUsageItems": usage_items,
        })
    # 采购中心的显式入库关联也合并进同一行（旧链路表仍兼容）。
    if external is not None:
        for link in db.query(InboundLink).filter_by(po_id=external.id).all():
            doc = db.query(JackyunGoodsDocument).filter_by(
                document_type="inbound", goodsdoc_no=link.goodsdoc_no
            ).first()
            if doc is None or doc.goodsdoc_no in seen_inbound:
                continue
            seen_inbound.add(doc.goodsdoc_no)
            inbound_info.append({
                "linkId": None,
                "targetId": doc.id,
                "goodsdocNo": doc.goodsdoc_no,
                "date": doc.document_at.isoformat() if doc.document_at else (
                    link.inbound_at.isoformat() if link.inbound_at else None
                ),
                "warehouseName": doc.warehouse_name or "",
                "supplier": doc.supplier_name or doc.company_name or "",
                "amount": _num(doc.total_amount),
                "matchMethod": "legacy",
                "confidence": None,
                "note": "采购中心历史入库关联",
                "consumableUsageDecided": False,
                "consumableUsageEnabled": None,
                "consumableUsageItems": [],
            })

    settlement_info: list[dict] = []
    seen_settlements: set[int] = set()
    for link in settlements:
        if link.target_id in seen_settlements:
            continue
        seen_settlements.add(link.target_id)
        settle = _settlement_of(db, pf, link.target_id)
        if settle is None:
            continue
        paid_amount = parse_decimal(settle.paid)
        settlement_info.append({
            "linkId": link.id,
            "targetId": settle.id,
            "settlementNo": settle.settlement_no,
            "date": settle.settlement_date.isoformat() if settle.settlement_date else None,
            "amount": float(settle.settlement_amount) if settle.settlement_amount is not None else None,
            "paidAmount": float(settle.paid) if settle.paid is not None else None,
            "status": settle.status or "",
            "paid": paid_amount is not None and paid_amount > 0,
            "matchMethod": link.match_method or "",
            "confidence": _num(link.confidence),
            "note": link.note or "",
        })

    invoice_info: list[dict] = []
    verified = False
    seen_invoice_ids: set[int] = set()
    # 已收票金额：优先用关联的分摊额（一票挂多单场景），未分摊才用票面全额。
    invoiced_amount = Decimal("0")
    for link in invoice_links:
        inv = _invoice_of(db, pf, link.invoice_id)
        if inv is None or inv.id in seen_invoice_ids:
            continue
        seen_invoice_ids.add(inv.id)
        if inv.verified:
            verified = True
        share = link.allocated_amount if link.allocated_amount is not None else inv.total_amount
        if share is not None:
            invoiced_amount += Decimal(str(share))
        invoice_info.append({
            **_serialize_invoice(inv),
            "linkId": link.id,
            "invoiceKind": "tax",
            "confirmed": True,
        })

    if external is not None:
        tax_numbers = {str(inv.get("invoiceNo")) for inv in invoice_info}
        for link, inv in db.query(PurchaseInvoiceLink, PurchaseInvoice).join(
            PurchaseInvoice, PurchaseInvoice.id == PurchaseInvoiceLink.invoice_id
        ).filter(PurchaseInvoiceLink.po_id == external.id).all():
            if inv.invoice_no and inv.invoice_no in tax_numbers:
                continue  # 税务原始清单优先，避免登记副本重复计票。
            invoice_info.append({"invoiceId": inv.id, "linkId": link.id, "invoiceKind": "manual",
                                 "invoiceNo": inv.invoice_no, "amount": _num(link.allocated_amount),
                                 "issueDate": inv.invoice_date.isoformat() if inv.invoice_date else None,
                                 "verified": False, "verifiedMonth": "", "confirmed": True})
            if link.allocated_amount is not None:
                invoiced_amount += Decimal(str(link.allocated_amount))
    verified = bool(invoice_info) and all(i["verified"] for i in invoice_info)

    allocations: list[dict] = []
    purchase_orders: list[dict] = []
    purchase_status = ""
    purchase_content_complete = False
    unallocated_amount = None
    if external is not None:
        allocation_rows = (
            pf.allocations.get(external.id, []) if pf is not None
            else db.query(PurchaseAllocationItem).filter_by(po_id=external.id).all()
        )
        allocations = [_serialize_allocation(row) for row in allocation_rows]
        po_links = (
            pf.po_links.get(external.id, []) if pf is not None
            else db.query(JackyunPurchaseOrderLink).filter_by(po_id=external.id).all()
        )
        purchase_orders = []
        for link in po_links:
            jpo = pf.jackyun_pos.get(link.jackyun_po_id) if pf is not None else db.get(JackyunPurchaseOrder, link.jackyun_po_id)
            if jpo is not None:
                purchase_orders.append({
                    **_serialize_jackyun_purchase(jpo),
                    "linkId": link.id,
                    "relationKind": link.relation_kind or "",
                    "allocAmount": _num(link.alloc_amount),
                })
        expenses = (
            pf.expenses.get(external.id, []) if pf is not None
            else db.query(PurchaseExtraExpense).filter_by(po_id=external.id).all()
        )
    else:
        expenses = []

    if external is not None:
        balance = balance_check(
            external.effective_paid_amount,
            [row.amount for row in allocation_rows],
            [row.amount for row in expenses],
        )
        unallocated_amount = _num(balance["unallocated"])
        purchase_status = external.purchase_status or ""
        purchase_content_complete = purchase_status != "pending_refine"

    amount = parse_decimal(order.actual_payment) if order is not None else None
    if amount is None and external is not None:
        amount = parse_decimal(external.paid_amount) or parse_decimal(external.order_amount)
    # 未开票金额 = 实付 - 已收票。采购员只负责催开票，认证（勾选抵扣）归财务，
    # 因此这里的口径只看票有没有开够，不看是否认证（2026-09-07 用户口径）。
    invoice_outstanding = None
    invoice_status = "none"
    if amount is not None:
        gap = Decimal(str(amount)) - invoiced_amount
        invoice_outstanding = float(gap) if gap > 0 else 0.0
        if invoiced_amount <= 0:
            invoice_status = "pending"        # 完全未开票 → 待供应商开票
        elif gap > 0:
            invoice_status = "partial"        # 部分开票 → 还差一部分
        else:
            invoice_status = "done"           # 票已开够
    order_date = _order_date_str(order) if order is not None else None
    # 1688 导出文件常见缺失 order_time 字段：用导入时间 created_at 兜底，
    # 保证列表页时间分组与排序在测试数据上也能展示出顺序。
    if order_date is None and order is not None and order.created_at is not None:
        order_date = order.created_at.isoformat()
    if order_date is None and external is not None:
        # 工作流副本可能来自已归档/删除的原始导入；保留其订单时间，缺失时才退回同步时间，
        # 让采购台仍能按真实可追溯时间排序，同时不把空时间伪造成业务完成。
        fallback_time = external.ordered_at or external.created_at
        order_date = fallback_time.isoformat() if fallback_time else None
    supplier = (
        (order.seller_company_name if order is not None else "")
        or (external.supplier_name if external is not None else "")
    )
    buyer = order.buyer_company_name if order is not None else (
        external.buyer_account if external is not None else ""
    )
    row = {
        # 兼容旧前端：orderId 优先使用文件副本 id；工作流独有订单使用采购单 id。
        "orderId": file_order_id if file_order_id is not None else external.id,
        "fileOrderId": file_order_id,
        "externalPoId": external.id if external is not None else None,
        "source": "file" if order is not None else "workflow",
        "platform": "1688" if order is not None else (external.platform or "other"),
        "orderNo": order_no,
        # 订单类型人工覆盖（goods/consumable/空）：自动判定仅作默认，见 _order_kind
        "orderKindOverride": (external.order_kind_override or "") if external is not None else "",
        "supplier": supplier,
        "buyer": buyer,
        "amount": float(amount) if amount is not None else None,
        "paidAmount": _num(external.paid_amount) if external is not None else (
            _num(order.actual_payment) if order is not None else None
        ),
        # 1688 是担保交易：下单即付款，pay_time + actual_payment 本身就是付款事实。
        # 本账号吉客云没有采购/结算体系（jackyun_purchase_settlements 恒为 0 条），
        # 不能因为拿不到结算单就把「付款」环节永久卡住（2026-09-06 口径）。
        "paidOn1688": bool(
            order is not None
            and order.pay_time is not None
            and (parse_decimal(order.actual_payment) or Decimal("0")) > 0
        ),
        "paidOn1688At": order.pay_time.isoformat() if (order is not None and order.pay_time) else None,
        "adjustmentAmount": _num(external.adjustment_amount) if external is not None else None,
        "adjustmentNote": (external.adjustment_note or "") if external is not None else "",
        "title": external.title if external is not None else "",
        "orderStatus": (order.order_status if order is not None else external.order_status) or "",
        "orderDate": order_date,
        # 金额拆分层：1688 订单本身的商品/运费/优惠，供采购工作台「金额与付款分层」表使用。
        # external 端（采购中心工作流）暂无这三个字段，回退为 None（金额分层退化为只显示应付款）。
        "goodsTotal": _num(order.goods_total) if order is not None else _num((external.raw or {}).get("goods_total")),
        "freight": _num(order.freight) if order is not None else _num((external.raw or {}).get("freight")),
        "discount": _num(order.discount) if order is not None else _num((external.raw or {}).get("discount")),
        "purchaseStatus": purchase_status,
        "purchaseContentComplete": purchase_content_complete,
        "unallocatedAmount": unallocated_amount,
        # 开票进度（采购员口径：只看票开没开够，认证归财务）。
        # pending=完全未开票 / partial=部分开票 / done=已开够 / none=无实付金额可比
        "invoiceStatus": invoice_status,
        "invoicedAmount": float(invoiced_amount),
        "invoiceOutstanding": invoice_outstanding,
        # 1688 原始报文里的货品明细（含编号/品名/数量/单价）。
        # 「确认采购内容」环节在没有入库单反填的分配行时用它兜底展示编号，
        # 避免用户看到空的「待确认采购内容」（2026-09-07）。
        "orderItems": _extract_1688_items(order),
        "allocations": allocations,
        # 耗材收货（本平台，不生成吉客云单据）：已收货的耗材单视为内容已确认 + 已入库。
        "consumable": _consumable_of(db, order, order_no),
        "expenses": [_serialize_expense(e) for e in expenses],
        "purchaseOrders": purchase_orders,
        # 采购单步骤按 Excel 口径跳过（2026-09-06 用户口径：金额以导入表格为准、入库闭环即放行）。
        "jackyunPoBypassed": bool(external is not None and (external.raw or {}).get("jackyunPoBypassed")),
        # 采购单金额闭环：关联了多张采购单（合并/拆分）时，Σ 分摊金额 vs 订单实付
        # 分摊金额未填的关联用采购单全额兜底；差额超 2% 提示可能存在合并/拆分未完整关联。
        "poAmountClosure": _po_amount_closure(purchase_orders, amount),
        "inbound": inbound_info,
        "settlement": settlement_info,
        "invoice": invoice_info,
        "verified": verified,
        "pendingCount": pending,
        "stageTotal": STAGE_TOTAL,
    }
    # 7 环节完成状态：① 1688 订单 → ② SKU → ③ 吉客云采购单 → ④ 入库 → ⑤ 发票 → ⑥ 付款 → ⑦ 认证
    row["stages"] = _stage_states(row)
    row["doneCount"] = sum(1 for s in row["stages"] if s["done"])
    return row


def _po_amount_closure(purchase_orders: list[dict], order_amount) -> dict:
    """采购单金额闭环核对：Σ(每条关联的分摊金额，未填分摊用采购单全额) vs 1688 订单实付。

    多张关联（合并/拆分）且差额超 2% 时 closed=False，提示人工标注分摊金额。
    单张关联且未标注时只做全额比对（差额大也不算异常——历史数据可能是运费/抵扣）。
    """
    if not purchase_orders:
        return {"relevant": False, "allocTotal": None, "gap": None, "closed": True}
    total = 0.0
    for po in purchase_orders:
        alloc = po.get("allocAmount")
        if alloc is not None and alloc > 0:
            total += float(alloc)
        elif po.get("amount") is not None:
            total += float(po["amount"])
    if order_amount is None:
        return {"relevant": True, "allocTotal": round(total, 2), "gap": None, "closed": True}
    gap = round(float(order_amount) - total, 2)
    closed = abs(gap) <= max(float(order_amount) * 0.02, 0.05)
    return {"relevant": True, "allocTotal": round(total, 2), "gap": gap, "closed": closed}


def _order_date_str(order: Alibaba1688Order) -> str | None:
    t = order_time(order)
    return t.isoformat() if t else None


def _num(v) -> float | None:
    """Decimal → float，空值保持 None。"""
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _stage_states(row: dict) -> list[dict]:
    """把一行聚合结果折算为 7 环节完成状态。

    列表页漏斗、行内状态点和详情页共用同一份口径，避免前后端各算一套。
    每项返回：{key, no, label, short, dimension, done, detail, amount}
    """
    allocations = row.get("allocations") or []
    purchase_orders = row.get("purchaseOrders") or []
    inbound = row.get("inbound") or []
    invoice = row.get("invoice") or []
    settlement = row.get("settlement") or []
    inbound_ready = bool(inbound) and all(item.get("consumableUsageDecided") for item in inbound)

    paid_rows = [s for s in settlement if s.get("paid")]
    paid_amount = sum(s.get("paidAmount") or s.get("amount") or 0 for s in paid_rows)
    verified_rows = [i for i in invoice if i.get("verified")]
    paid_on_1688 = bool(row.get("paidOn1688"))
    paid_on_1688_at = (row.get("paidOn1688At") or "")[:10]
    if paid_rows:
        paid_detail = f"已付 {len(paid_rows)} 笔"
    elif paid_on_1688:
        paid_detail = f"1688 担保交易已实付{f'（{paid_on_1688_at}）' if paid_on_1688_at else ''}"
    elif settlement:
        paid_detail = "有结算单未付款"
    else:
        paid_detail = "未付款"

    first_po_no = (purchase_orders[0].get("purchNo") if purchase_orders else "") or "—"
    # 吉客云无采购单体系时（2026-09-06 用户口径：金额以导入表格为准、入库已闭环），
    # 采购单步骤按口径跳过即视为完成。
    jackyun_po_bypassed = bool(row.get("jackyunPoBypassed"))
    # 耗材线（本平台 HC 单）：收齐即内容已确认/无需吉客云货品/不生成采购单/收货即入库
    # （与 workbench _step_states 同口径，2026-09-07）。
    consumable_received = bool((row.get("consumable") or {}).get("received"))
    states: dict[str, tuple[bool, str, float | None]] = {
        # ① 有行即有单：链路的起点恒为已完成
        "order": (True, row.get("orderNo") or "—", row.get("amount")),
        # ② SKU 匹配只看每条分配是否落到真实 SKU；金额平衡/采购内容确认是后续可编辑状态，不能反向把已匹配 SKU 算成未匹配。
        "sku": (
            (bool(allocations) and all(a.get("skuId") for a in allocations)) or consumable_received,
            (
                f"{sum(1 for a in allocations if a.get('skuId'))}/{len(allocations)} 个 SKU 已匹配"
                if allocations
                else ("耗材（本平台）无需吉客云货品" if consumable_received else "待细化采购内容")
            ),
            sum(a.get("amount") or 0 for a in allocations) or None,
        ),
        "jackyunPo": (
            bool(purchase_orders) or jackyun_po_bypassed or consumable_received,
            (first_po_no if purchase_orders else ("耗材不生成吉客云采购单" if consumable_received else "已按 Excel 口径跳过（入库闭环）"))
            if (purchase_orders or jackyun_po_bypassed or consumable_received) else "未生成采购单",
            sum(p.get("amount") or 0 for p in purchase_orders) or None,
        ),
        "inbound": (
            inbound_ready or consumable_received,
            (
                "耗材已收货入库" if consumable_received
                else (f"{len(inbound)} 张入库单，耗材使用已确认" if inbound_ready else (f"{len(inbound)} 张入库单，待确认耗材使用" if inbound else "未入库"))
            ),
            None,
        ),
        "invoice": (
            bool(invoice),
            f"{len(invoice)} 张发票" if invoice else "未收票",
            sum(i.get("amount") or 0 for i in invoice) or None,
        ),
        "paid": (
            bool(paid_rows) or paid_on_1688,
            paid_detail,
            paid_amount or (row.get("paidAmount") if paid_on_1688 else None),
        ),
        "verified": (
            bool(verified_rows),
            f"已认证 {verified_rows[0].get('verifiedMonth') or ''}".strip() if verified_rows else "未认证",
            sum(i.get("amount") or 0 for i in verified_rows) or None,
        ),
    }

    out: list[dict] = []
    for stage in CHAIN_STAGES:
        done, detail, amount = states[stage["key"]]
        out.append({**stage, "done": done, "detail": detail, "amount": amount})
    return out


def stage_done_map(row: dict) -> dict[str, bool]:
    """环节 key → 是否完成，供统计复用。"""
    return {s["key"]: s["done"] for s in row.get("stages") or []}


def list_all_records(db: Session, limit: int = 300, offset: int = 0) -> dict:
    """平铺全量记录：把 1688 订单、吉客云入库单/结算单、税务发票全部放进一张表，每行标记数据维度与环节。

    - dimension: 1688 / jackyun / tax（即「1688 维度 / 吉客云维度 / 税务维度」）
    - stage: order / inbound / settlement / invoice（① 采购 ② 入库 ③ 发票 ④ 付款 ⑤ 认证）
    - linkedOrderNos: 该单据已确认关联的 1688 订单号；pendingCount 为未确认关联数
    """
    rows: list[dict] = []

    # ① 1688 采购订单（仅展示已确认 active 导入带来的、未被用户删除的订单）
    for order in filter_active_rows(
        filter_active_import(
            db.query(Alibaba1688Order), Alibaba1688Order, Alibaba1688FileImport, Alibaba1688Order.import_id
        ),
        Alibaba1688Order,
    ).all():
        rows.append({
            "recordId": f"order-{order.id}",
            "dimension": "1688",
            "stage": "order",
            "no": order.external_order_id or f"#{order.id}",
            "counterparty": order.seller_company_name or "",
            "amount": _num(order.actual_payment),
            "date": _order_date_str(order),
            "status": order.order_status or "",
            "statusTone": "info",
            "linkedOrderNos": [],
            "pendingCount": 0,
            "invoiceId": None,
            "verified": False,
            "verifiedMonth": "",
        })

    # ② 吉客云 入库单
    for doc in db.query(JackyunGoodsDocument).filter_by(document_type="inbound").all():
        links = db.query(ProcurementChainLink).filter_by(target_type="inbound", target_id=doc.id).filter(
            ProcurementChainLink.match_method != "rejected"
        ).all()
        linked: list[str] = []
        pending = 0
        for link in links:
            order = db.get(Alibaba1688Order, link.order_id)
            if order is None or order.row_status == "deleted":
                continue
            if link.confirmed:
                linked.append(order.external_order_id or f"#{order.id}")
            else:
                pending += 1
        rows.append({
            "recordId": f"inbound-{doc.id}",
            "dimension": "jackyun",
            "stage": "inbound",
            "no": doc.goodsdoc_no,
            "counterparty": doc.supplier_name or doc.company_name or "",
            "amount": _num(doc.total_amount),
            "date": doc.document_at.isoformat() if doc.document_at else None,
            "status": doc.warehouse_name or "已入库",
            "statusTone": "ok",
            "linkedOrderNos": linked,
            "pendingCount": pending,
            "invoiceId": None,
            "verified": False,
            "verifiedMonth": "",
        })

    # ④ 吉客云 结算 / 付款
    for st in db.query(JackyunPurchaseSettlement).all():
        links = db.query(ProcurementChainLink).filter_by(target_type="settlement", target_id=st.id).filter(
            ProcurementChainLink.match_method != "rejected"
        ).all()
        linked = []
        pending = 0
        for link in links:
            order = db.get(Alibaba1688Order, link.order_id)
            if order is None or order.row_status == "deleted":
                continue
            if link.confirmed:
                linked.append(order.external_order_id or f"#{order.id}")
            else:
                pending += 1
        paid = st.paid is not None and st.paid > 0
        amount = st.settlement_amount if st.settlement_amount is not None else st.total_amount
        rows.append({
            "recordId": f"settle-{st.id}",
            "dimension": "jackyun",
            "stage": "settlement",
            "no": st.settlement_no,
            "counterparty": st.supplier_name or st.company_name or "",
            "amount": _num(amount),
            "date": st.settlement_date.isoformat() if st.settlement_date else None,
            "status": "已付款" if paid else (st.status or "未付款"),
            "statusTone": "ok" if paid else "warn",
            "linkedOrderNos": linked,
            "pendingCount": pending,
            "invoiceId": None,
            "verified": False,
            "verifiedMonth": "",
        })

    # ③ 税务发票（含 ⑤ 认证状态）
    for inv in filter_visible_invoices(
        db.query(TaxInvoice).filter_by(direction="input")
    ).all():
        links = db.query(TaxInvoiceLink).filter_by(invoice_id=inv.id, target_type="alibaba1688_order").filter(
            TaxInvoiceLink.match_method != "rejected"
        ).all()
        linked = []
        pending = 0
        for link in links:
            order = db.get(Alibaba1688Order, link.target_id)
            if order is None or order.row_status == "deleted":
                continue
            if link.confirmed:
                linked.append(order.external_order_id or f"#{order.id}")
            else:
                pending += 1
        rows.append({
            "recordId": f"invoice-{inv.id}",
            "dimension": "tax",
            "stage": "invoice",
            "no": f"{inv.invoice_code or ''}{inv.invoice_number or ''}",
            "counterparty": inv.seller_name or "",
            "amount": _num(inv.total_amount),
            "date": inv.issue_date.isoformat() if inv.issue_date else None,
            "status": f"已认证 {inv.verified_month}".strip() if inv.verified else "未认证",
            "statusTone": "ok" if inv.verified else "warn",
            "linkedOrderNos": linked,
            "pendingCount": pending,
            "invoiceId": inv.id,
            "verified": inv.verified,
            "verifiedMonth": inv.verified_month or "",
        })

    rows.sort(key=lambda r: r["date"] or "", reverse=True)
    total = len(rows)
    return {"total": total, "items": rows[offset:offset + limit]}


def get_order_detail(db: Session, order_id: int) -> dict | None:
    """单个 1688 订单的链路详情（二级页面用）：五环节明细 + 本单待确认建议。"""
    order = db.get(Alibaba1688Order, order_id)
    if order is None:
        return None
    row = _order_row(db, order)

    suggestions: list[dict] = []
    # 入库 / 结算建议
    for link in db.query(ProcurementChainLink).filter_by(order_id=order.id, confirmed=False).filter(
        ProcurementChainLink.match_method != "rejected"
    ).all():
        target = _chain_target(db, link.target_type, link.target_id)
        fields = _chain_target_fields(link.target_type, target)
        suggestions.append({
            "kind": "chain" if link.target_type == "inbound" else "settlement",
            "linkId": link.id,
            "targetId": link.target_id,
            "targetType": link.target_type,
            **fields,
            "confidence": float(link.confidence) if link.confidence is not None else None,
            "note": link.note or "",
        })
    # 发票建议
    for link in db.query(TaxInvoiceLink).filter_by(
        target_type="alibaba1688_order", target_id=order.id, confirmed=False
    ).filter(TaxInvoiceLink.match_method != "rejected").all():
        inv = db.get(TaxInvoice, link.invoice_id)
        suggestions.append({
            "kind": "invoice",
            "linkId": link.id,
            "targetId": link.invoice_id,
            "targetType": "invoice",
            "targetNo": _invoice_no(inv) if inv is not None else str(link.invoice_id),
            "targetAmount": float(inv.total_amount) if (inv is not None and inv.total_amount is not None) else None,
            "targetDate": inv.issue_date.isoformat() if (inv is not None and inv.issue_date) else None,
            "targetSupplier": inv.seller_name if inv is not None else "",
            "confidence": float(link.confidence) if link.confidence is not None else None,
            "note": link.note or "",
        })
    row["suggestions"] = suggestions
    return row


def overview(db: Session) -> dict:
    """链路漏斗统计，与详情表共用同一行级聚合，避免两套口径。"""
    rows = [_order_row(db, order, external) for order, external in _source_pairs(db)]
    total = len(rows)
    done_by_stage: dict[str, int] = {stage["key"]: 0 for stage in CHAIN_STAGES}
    for row in rows:
        for key, done in stage_done_map(row).items():
            if done:
                done_by_stage[key] += 1

    # 7 环节漏斗（前端按顺序渲染）
    stages = [
        {
            **stage,
            "count": done_by_stage[stage["key"]],
            "pct": round(done_by_stage[stage["key"]] / total * 100) if total else 0,
        }
        for stage in CHAIN_STAGES
    ]

    def _count(key: str) -> int:
        return done_by_stage[key]

    return {
        "total": total,
        "stageTotal": STAGE_TOTAL,
        "stages": stages,
        # 兼容旧前端字段：命名保持不变，值统一从同一套环节口径派生
        "refined": _count("sku"),
        "jackyunLinked": _count("jackyunPo"),
        "inbound": _count("inbound"),
        "invoiced": _count("invoice"),
        "paid": _count("paid"),
        "verified": _count("verified"),
        "pending": sum(row["pendingCount"] for row in rows),
    }


def list_pending(db: Session) -> dict:
    """待确认的关联建议，供人工确认或拒绝。"""
    out: list[dict] = []
    for link in db.query(ProcurementChainLink).filter_by(confirmed=False).filter(
        ProcurementChainLink.match_method != "rejected"
    ).all():
        order = db.get(Alibaba1688Order, link.order_id)
        if order is None or order.row_status == "deleted":
            continue
        target = _chain_target(db, link.target_type, link.target_id)
        if target is None:
            continue
        fields = _chain_target_fields(link.target_type, target)
        out.append({
            "kind": "chain" if link.target_type == "inbound" else "settlement",
            "linkId": link.id,
            "orderId": order.id,
            "orderNo": order.external_order_id,
            "supplier": order.seller_company_name or "",
            "targetId": link.target_id,
            "targetType": link.target_type,
            **fields,
            "confidence": float(link.confidence) if link.confidence is not None else None,
            "note": link.note or "",
        })
    for link in db.query(TaxInvoiceLink).filter(
        TaxInvoiceLink.target_type.in_(("alibaba1688_order", "external_purchase_order")),
        TaxInvoiceLink.confirmed.is_(False),
        TaxInvoiceLink.match_method != "rejected",
    ).all():
        order = None
        external = None
        if link.target_type == "alibaba1688_order":
            order = db.get(Alibaba1688Order, link.target_id)
            if order is not None and order.row_status != "deleted":
                external = db.query(ExternalPurchaseOrder).filter_by(
                    external_order_id=order.external_order_id
                ).first()
        else:
            external = db.get(ExternalPurchaseOrder, link.target_id)
            if external is not None:
                order = db.query(Alibaba1688Order).filter_by(
                    external_order_id=external.external_order_id
                ).first()
        inv = db.get(TaxInvoice, link.invoice_id)
        if (order is None and external is None) or inv is None:
            continue
        out.append({
            "kind": "invoice",
            "linkId": link.id,
            "orderId": order.id if order is not None else external.id,
            "orderNo": order.external_order_id if order is not None else external.external_order_id,
            "supplier": (order.seller_company_name if order is not None else external.supplier_name) or "",
            "targetId": inv.id,
            "targetType": "invoice",
            "targetNo": _invoice_no(inv),
            "targetAmount": float(inv.total_amount) if inv.total_amount is not None else None,
            "targetDate": inv.issue_date.isoformat() if inv.issue_date else None,
            "targetSupplier": inv.seller_name or "",
            "confidence": float(link.confidence) if link.confidence is not None else None,
            "note": link.note or "",
        })
    return {"items": out}


# ═══════════════════════════════════════════════════════════════
# 采购执行中心：订单时间为主轴，供应商为聚合维度
# ═══════════════════════════════════════════════════════════════


def execution_overview(db: Session) -> dict:
    """采购执行中心顶部 5 张统计卡。

    口径：以 1688 订单为起点，按"接下来该做什么"分组。
    """
    rows = [_order_row(db, order, external) for order, external in _source_pairs(db)]
    pending_process = 0       # 还没进入采购工作流：只有 1688 文件订单
    pending_refine = 0        # 工作流存在但 purchase_status == pending_refine
    pending_sku = 0           # 已确认采购内容但还没分配 SKU
    pending_po = 0            # 已分配 SKU 但还没生成吉客云采购单
    pending_invoice = 0       # 已入库/已付款但还没发票

    for row in rows:
        external = db.query(ExternalPurchaseOrder).filter_by(
            external_order_id=row["orderNo"]
        ).first() if row["source"] == "file" else None
        has_external = external is not None or row["source"] == "workflow"

        stages = stage_done_map(row)
        if not has_external:
            pending_process += 1
            continue

        purchase_status = row.get("purchaseStatus") or ""
        allocations = row.get("allocations") or []
        purchase_orders = row.get("purchaseOrders") or []

        if purchase_status == "pending_refine":
            pending_refine += 1
        elif not allocations:
            pending_sku += 1
        elif not purchase_orders:
            pending_po += 1
        elif (stages.get("inbound") or stages.get("paid")) and not stages.get("invoice"):
            pending_invoice += 1

    return {
        "totalOrders": len(rows),
        "pendingProcess": pending_process,
        "pendingRefine": pending_refine,
        "pendingSku": pending_sku,
        "pendingPo": pending_po,
        "pendingInvoice": pending_invoice,
    }


def supplier_summaries(db: Session, limit: int = 200, offset: int = 0) -> dict:
    """供应商聚合列表：管理视角。"""
    rows = [_order_row(db, order, external) for order, external in _source_pairs(db)]
    by_supplier: dict[str, dict] = {}
    sku_counter: dict[str, dict[str, dict]] = {}

    for row in rows:
        supplier = (row.get("supplier") or row.get("buyer") or "未命名供应商").strip()
        if not supplier:
            supplier = "未命名供应商"
        if supplier not in by_supplier:
            by_supplier[supplier] = {
                "supplierName": supplier,
                "orderCount": 0,
                "totalPurchase": 0.0,
                "uninvoiced": 0.0,
                "uninbound": 0.0,
                "lastOrderDate": None,
            }
            sku_counter[supplier] = {}

        info = by_supplier[supplier]
        info["orderCount"] += 1
        amount = row.get("amount") or 0
        info["totalPurchase"] += amount

        stages = stage_done_map(row)
        if not stages.get("invoice") and amount:
            # 简单口径：只要没有收到发票，整单金额计入待开票
            info["uninvoiced"] += amount
        if not stages.get("inbound") and amount:
            info["uninbound"] += amount

        date = row.get("orderDate")
        if date and (info["lastOrderDate"] is None or date > info["lastOrderDate"]):
            info["lastOrderDate"] = date

        for alloc in row.get("allocations") or []:
            sku_code = alloc.get("skuCode") or "未编码商品"
            name = alloc.get("goodsName") or sku_code
            if sku_code not in sku_counter[supplier]:
                sku_counter[supplier][sku_code] = {"skuCode": sku_code, "goodsName": name, "count": 0}
            sku_counter[supplier][sku_code]["count"] += 1

    items = []
    for supplier in sorted(by_supplier.keys()):
        info = by_supplier[supplier]
        often = sorted(sku_counter[supplier].values(), key=lambda x: x["count"], reverse=True)[:5]
        items.append({
            **info,
            "totalPurchase": round(info["totalPurchase"], 2),
            "uninvoiced": round(info["uninvoiced"], 2),
            "uninbound": round(info["uninbound"], 2),
            "oftenSkus": often,
        })

    return {"total": len(items), "items": items[offset:offset + limit]}


def supplier_detail(db: Session, supplier_name: str) -> dict | None:
    """单个供应商详情：历史合作 + 最近订单 + 常购 SKU。"""
    rows = [_order_row(db, order, external) for order, external in _source_pairs(db)]
    matched = [r for r in rows if (r.get("supplier") or r.get("buyer") or "").strip() == supplier_name]
    if not matched:
        return None

    matched.sort(key=lambda r: r.get("orderDate") or "", reverse=True)
    total = sum((r.get("amount") or 0) for r in matched)
    uninvoiced = sum(
        (r.get("amount") or 0) for r in matched
        if not stage_done_map(r).get("invoice")
    )
    uninbound = sum(
        (r.get("amount") or 0) for r in matched
        if not stage_done_map(r).get("inbound")
    )

    sku_counter: dict[str, dict] = {}
    for r in matched:
        for alloc in r.get("allocations") or []:
            sku_code = alloc.get("skuCode") or "未编码商品"
            name = alloc.get("goodsName") or sku_code
            if sku_code not in sku_counter:
                sku_counter[sku_code] = {"skuCode": sku_code, "goodsName": name, "count": 0}
            sku_counter[sku_code]["count"] += 1

    often = sorted(sku_counter.values(), key=lambda x: x["count"], reverse=True)[:8]

    return {
        "supplierName": supplier_name,
        "orderCount": len(matched),
        "totalPurchase": round(total, 2),
        "uninvoiced": round(uninvoiced, 2),
        "uninbound": round(uninbound, 2),
        "lastOrderDate": matched[0].get("orderDate"),
        "oftenSkus": often,
        "recentOrders": matched[:10],
    }


def order_supplier_history(db: Session, supplier_name: str, exclude_order_id: int | None = None) -> dict:
    """某个订单的供应商历史：合作次数、累计金额、未开发票、最近采购、常购 SKU。"""
    rows = [_order_row(db, order, external) for order, external in _source_pairs(db)]
    matched = [
        r for r in rows
        if (r.get("supplier") or r.get("buyer") or "").strip() == supplier_name
        and (exclude_order_id is None or r.get("orderId") != exclude_order_id)
    ]
    matched.sort(key=lambda r: r.get("orderDate") or "", reverse=True)
    total = sum((r.get("amount") or 0) for r in matched)
    uninvoiced = sum(
        (r.get("amount") or 0) for r in matched
        if not stage_done_map(r).get("invoice")
    )

    sku_counter: dict[str, dict] = {}
    for r in matched:
        for alloc in r.get("allocations") or []:
            sku_code = alloc.get("skuCode") or "未编码商品"
            name = alloc.get("goodsName") or sku_code
            if sku_code not in sku_counter:
                sku_counter[sku_code] = {"skuCode": sku_code, "goodsName": name, "count": 0}
            sku_counter[sku_code]["count"] += 1

    often = sorted(sku_counter.values(), key=lambda x: x["count"], reverse=True)[:5]

    return {
        "orderCount": len(matched),
        "totalPurchase": round(total, 2),
        "uninvoiced": round(uninvoiced, 2),
        "lastOrderDate": matched[0].get("orderDate") if matched else None,
        "oftenSkus": often,
        "recentOrders": matched[:5],
    }
