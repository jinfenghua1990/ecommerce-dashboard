"""本平台耗材采购与收货，不生成吉客云单据。"""

from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin


class ConsumablePurchase(Base, PkMixin, TimestampMixin):
    __tablename__ = "consumable_purchases"
    __table_args__ = (
        Index("uq_consumable_purchase_source", "source_order_id", unique=True, postgresql_where=text("status <> 'cancelled'")),
    )

    number: Mapped[str] = mapped_column(String(40), unique=True)
    request_key: Mapped[str] = mapped_column(String(36), unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    supplier_name: Mapped[str] = mapped_column(String(256))
    ordered_on: Mapped[date] = mapped_column(Date)
    source_order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("alibaba1688_orders.id"), nullable=True)
    reference_no: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(16), default="ordered", index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(128), default="")


class ConsumablePurchaseItem(Base, PkMixin, TimestampMixin):
    __tablename__ = "consumable_purchase_items"
    __table_args__ = (
        UniqueConstraint("purchase_id", "consumable_id", name="uq_consumable_purchase_item"),
        CheckConstraint("quantity > 0 AND received_qty >= 0 AND received_qty <= quantity AND unit_cost >= 0", name="ck_consumable_purchase_quantities"),
    )

    purchase_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("consumable_purchases.id"), index=True)
    consumable_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("consumables.id"))
    code: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(256))
    unit: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    received_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    # 单价 10 位小数：总金额对齐订单实付时（如 800 ÷ 1050），4 位小数会产生分毫尾差，发票口径对不上。
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(18, 10))


class ConsumableReceipt(Base, PkMixin, TimestampMixin):
    __tablename__ = "consumable_receipts"

    purchase_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("consumable_purchases.id"), index=True)
    number: Mapped[str] = mapped_column(String(40), unique=True)
    request_key: Mapped[str] = mapped_column(String(36), unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    received_on: Mapped[date] = mapped_column(Date)
    # 到货位置：own=自有仓 / factory=工厂（耗材不进吉客云，收货直接加本平台对应库存）
    location: Mapped[str] = mapped_column(String(16), default="own", server_default="own")
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(128), default="")
