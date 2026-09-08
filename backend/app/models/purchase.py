from decimal import Decimal
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin

MONEY = Numeric(18, 4)

"""1688 只是“采购交易来源”（规格 1.2）：只读同步已发生的买家订单，不下单、不付款。"""


class Supplier(Base, PkMixin, TimestampMixin):
    __tablename__ = "suppliers"

    platform: Mapped[str] = mapped_column(String(32), default="1688")
    external_shop_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    name: Mapped[str] = mapped_column(String(256), index=True, nullable=False)
    contact: Mapped[str] = mapped_column(String(256), default="")


class ExternalPurchaseOrder(Base, PkMixin, TimestampMixin):
    """外部采购订单主档（1688 / 拼多多 / 淘宝 / 其他渠道）。

    ``external_order_id`` 是业务侧订单号；``platform`` 描述其来源。同一个订单号
    允许出现在不同渠道，幂等键为 ``platform + external_order_id``。1688 原始订单
    仍保存在独立的 ``alibaba1688_orders`` 表，这里承载统一的采购工作流。
    """

    __tablename__ = "external_purchase_orders"
    __table_args__ = (
        UniqueConstraint("platform", "external_order_id", name="uq_external_purchase_platform_order"),
    )

    external_order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), default="1688")
    buyer_account: Mapped[str] = mapped_column(String(128), default="")
    supplier_name: Mapped[str] = mapped_column(String(256), default="", index=True)
    title: Mapped[str] = mapped_column(Text, default="")  # 原始标题（可能为“定制专拍/OEM定制”）
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    order_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    paid_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    # 1688 微调金额：红包等导致开票金额（准确）与订单实付的零头差；分配平衡目标 = 实付 + 微调
    adjustment_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    adjustment_note: Mapped[str] = mapped_column(String(256), default="")
    # 订单类型人工覆盖：goods=正品 / consumable=耗材；空=按自动判定
    # （耗材档案 Excel 的「采购订货号」可能填错，自动判定仅作默认值）
    order_kind_override: Mapped[str] = mapped_column(String(16), default="")
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    order_status: Mapped[str] = mapped_column(String(64), default="", index=True)
    pay_status: Mapped[str] = mapped_column(String(64), default="")
    ship_status: Mapped[str] = mapped_column(String(64), default="")
    refund_status: Mapped[str] = mapped_column(String(64), default="")
    logistics: Mapped[dict] = mapped_column(JSONB, default=dict)
    # 采购状态机（规格 7.5）
    purchase_status: Mapped[str] = mapped_column(String(32), default="pending_refine", index=True)
    # 发票状态与采购状态分离
    invoice_status: Mapped[str] = mapped_column(String(32), default="unverified", index=True)
    allocated_goods_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    allocated_expense_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    refined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)

    @property
    def effective_paid_amount(self) -> Decimal | None:
        """分配平衡目标：实付 + 1688 微调（未微调时即实付）。"""
        if self.paid_amount is None:
            return None
        return self.paid_amount + (self.adjustment_amount or Decimal("0"))


class ExternalPurchaseOrderRawItem(Base, PkMixin):
    """1688 原始明细；不得直接建成吉客云 SKU（规格 20）。"""

    __tablename__ = "external_purchase_order_raw_items"

    po_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    raw_title: Mapped[str] = mapped_column(Text, default="")
    raw_spec: Mapped[str] = mapped_column(Text, default="")
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    raw_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class PurchaseAllocationItem(Base, PkMixin, TimestampMixin):
    """一笔外部采购订单 → N 个吉客云 SKU 分配（规格 14 关键关系）。"""

    __tablename__ = "purchase_allocation_items"

    po_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    sku_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    sku_code: Mapped[str] = mapped_column(String(128), default="", index=True)
    goods_name: Mapped[str] = mapped_column(String(512), default="")
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    # 匹配来源：manual=人工配置；auto=按供应商历史入库货品自动预填（仍需人工确认生效）
    source: Mapped[str] = mapped_column(String(16), default="manual")
    match_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    # 来源入库单明细行 ID（入库反填时写入）。默认自动反填仍保持单行排他；
    # 合并采购单场景可通过显式“入库分摊”把同一明细行按数量分给多个采购订单，
    # 但所有采购单分摊数量之和不得超过吉客云实际入库数量。
    source_item_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)


class PurchaseExtraExpense(Base, PkMixin, TimestampMixin):
    """附加费用不伪造成 SKU（规格 7.4）。"""

    __tablename__ = "purchase_extra_expenses"

    po_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    expense_type: Mapped[str] = mapped_column(String(32), nullable=False)  # pack/processing/plate/mold/freight/testing/other
    amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    allocate_method: Mapped[str] = mapped_column(String(32), default="none")  # by_qty/by_amount/manual/none
    distribute: Mapped[dict] = mapped_column(JSONB, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")


class PurchaseInvoice(Base, PkMixin, TimestampMixin):
    __tablename__ = "purchase_invoices"

    invoice_no: Mapped[str] = mapped_column(String(128), default="", index=True)
    supplier_name: Mapped[str] = mapped_column(String(256), default="")
    invoice_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    invoice_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    type: Mapped[str] = mapped_column(String(32), default="")  # special/vat/other
    file_path: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="received")


class PurchaseInvoiceLink(Base, PkMixin):
    """发票与采购单多对多（规格 7.5：一单多票、一票多单）。"""

    __tablename__ = "purchase_invoice_links"
    __table_args__ = (UniqueConstraint("invoice_id", "po_id", name="uq_invoice_po"),)

    invoice_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    po_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    allocated_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)


class JackyunPurchaseOrder(Base, PkMixin, TimestampMixin):
    __tablename__ = "jackyun_purchase_orders"

    jackyun_purch_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    purch_no: Mapped[str] = mapped_column(String(128), default="", index=True)
    supplier_name: Mapped[str] = mapped_column(String(256), default="")
    amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class JackyunPurchaseOrderLink(Base, PkMixin, TimestampMixin):
    __tablename__ = "jackyun_purchase_order_links"
    __table_args__ = (UniqueConstraint("po_id", "jackyun_po_id", name="uq_po_jackyunpo"),)

    po_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    jackyun_po_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    # 合并/拆分标注：'' 普通（一单一采购单）；merged 多张线上采购单共用一张吉客云采购单；
    # split 一张线上采购单拆成多张吉客云采购单。alloc_amount 为本订单在该采购单中的分摊金额，
    # 合并/拆分时用于金额闭环核对（Σ alloc_amount vs 订单实付）。
    relation_kind: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    alloc_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    note: Mapped[str] = mapped_column(String(256), default="", nullable=False)


class InboundLink(Base, PkMixin, TimestampMixin):
    __tablename__ = "inbound_links"

    po_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    goodsdoc_no: Mapped[str] = mapped_column(String(128), default="", index=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
