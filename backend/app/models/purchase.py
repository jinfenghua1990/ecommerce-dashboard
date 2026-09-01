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
    """1688 买家订单。external_order_id 唯一，重复同步只更新状态（规格 7.2 / 16）。"""

    __tablename__ = "external_purchase_orders"

    external_order_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(32), default="1688")
    buyer_account: Mapped[str] = mapped_column(String(128), default="")
    supplier_name: Mapped[str] = mapped_column(String(256), default="", index=True)
    title: Mapped[str] = mapped_column(Text, default="")  # 原始标题（可能为“定制专拍/OEM定制”）
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    order_amount: Mapped | None = mapped_column(MONEY, nullable=True)
    paid_amount: Mapped | None = mapped_column(MONEY, nullable=True)
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
    allocated_goods_amount: Mapped | None = mapped_column(MONEY, nullable=True)
    allocated_expense_amount: Mapped | None = mapped_column(MONEY, nullable=True)
    refined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class ExternalPurchaseOrderRawItem(Base, PkMixin):
    """1688 原始明细；不得直接建成吉客云 SKU（规格 20）。"""

    __tablename__ = "external_purchase_order_raw_items"

    po_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    raw_title: Mapped[str] = mapped_column(Text, default="")
    raw_spec: Mapped[str] = mapped_column(Text, default="")
    quantity: Mapped | None = mapped_column(Numeric(18, 4), nullable=True)
    raw_amount: Mapped | None = mapped_column(MONEY, nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class PurchaseAllocationItem(Base, PkMixin, TimestampMixin):
    """一笔 1688 订单 → N 个吉客云 SKU 分配（规格 14 关键关系）。"""

    __tablename__ = "purchase_allocation_items"

    po_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    sku_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    sku_code: Mapped[str] = mapped_column(String(128), default="", index=True)
    goods_name: Mapped[str] = mapped_column(String(512), default="")
    quantity: Mapped | None = mapped_column(Numeric(18, 4), nullable=True)
    unit_price: Mapped | None = mapped_column(MONEY, nullable=True)
    amount: Mapped | None = mapped_column(MONEY, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")


class PurchaseExtraExpense(Base, PkMixin, TimestampMixin):
    """附加费用不伪造成 SKU（规格 7.4）。"""

    __tablename__ = "purchase_extra_expenses"

    po_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    expense_type: Mapped[str] = mapped_column(String(32), nullable=False)  # pack/processing/plate/mold/freight/testing/other
    amount: Mapped | None = mapped_column(MONEY, nullable=True)
    allocate_method: Mapped[str] = mapped_column(String(32), default="none")  # by_qty/by_amount/manual/none
    distribute: Mapped[dict] = mapped_column(JSONB, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")


class PurchaseInvoice(Base, PkMixin, TimestampMixin):
    __tablename__ = "purchase_invoices"

    invoice_no: Mapped[str] = mapped_column(String(128), default="", index=True)
    supplier_name: Mapped[str] = mapped_column(String(256), default="")
    invoice_amount: Mapped | None = mapped_column(MONEY, nullable=True)
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
    allocated_amount: Mapped | None = mapped_column(MONEY, nullable=True)


class JackyunPurchaseOrder(Base, PkMixin, TimestampMixin):
    __tablename__ = "jackyun_purchase_orders"

    jackyun_purch_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    purch_no: Mapped[str] = mapped_column(String(128), default="", index=True)
    supplier_name: Mapped[str] = mapped_column(String(256), default="")
    amount: Mapped | None = mapped_column(MONEY, nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class JackyunPurchaseOrderLink(Base, PkMixin, TimestampMixin):
    __tablename__ = "jackyun_purchase_order_links"
    __table_args__ = (UniqueConstraint("po_id", "jackyun_po_id", name="uq_po_jackyunpo"),)

    po_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    jackyun_po_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)


class InboundLink(Base, PkMixin, TimestampMixin):
    __tablename__ = "inbound_links"

    po_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    goodsdoc_no: Mapped[str] = mapped_column(String(128), default="", index=True)
    quantity: Mapped | None = mapped_column(Numeric(18, 4), nullable=True)
    inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
