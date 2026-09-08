"""供应链中心：委外生产订单与耗材预占。

生产单代表“补货计划选择工厂生产”后的执行单据。耗材预占只占用可用量，
不直接改动 consumables.stock_qty；实际发往工厂时再通过耗材流水扣减自有仓。
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin


class ProductionOrder(Base, PkMixin, TimestampMixin):
    __tablename__ = "production_orders"
    __table_args__ = (
        Index("ix_production_orders_status_expected", "status", "expected_delivery_date"),
    )

    order_no: Mapped[str] = mapped_column(String(40), unique=True)
    factory_name: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(24), default="planned", index=True)
    planned_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), default="manual")
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(128), default="")


class ProductionOrderItem(Base, PkMixin, TimestampMixin):
    __tablename__ = "production_order_items"
    __table_args__ = (
        UniqueConstraint("production_order_id", "sku_id", name="uq_production_order_item_sku"),
        CheckConstraint(
            "quantity > 0 AND completed_qty >= 0 AND completed_qty <= quantity",
            name="ck_production_order_item_quantities",
        ),
    )

    production_order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("production_orders.id"), index=True)
    sku_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("product_skus.id"), index=True)
    sku_code: Mapped[str] = mapped_column(String(128))
    sku_name: Mapped[str] = mapped_column(String(256), default="")
    unit: Mapped[str] = mapped_column(String(32), default="")
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    completed_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))


class ProductionMaterialReservation(Base, PkMixin, TimestampMixin):
    __tablename__ = "production_material_reservations"
    __table_args__ = (
        UniqueConstraint("production_order_id", "consumable_id", name="uq_production_material_order_consumable"),
        CheckConstraint(
            "required_qty > 0 AND reserved_qty >= 0 AND dispatched_qty >= 0 "
            "AND factory_received_qty >= 0 AND consumed_qty >= 0 "
            "AND reserved_qty + dispatched_qty <= required_qty "
            "AND factory_received_qty <= dispatched_qty "
            "AND consumed_qty <= factory_received_qty",
            name="ck_production_material_quantities",
        ),
    )

    production_order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("production_orders.id"), index=True)
    consumable_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("consumables.id"), index=True)
    code: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(256))
    unit: Mapped[str] = mapped_column(String(32), default="个")
    required_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    reserved_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    dispatched_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    factory_received_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    consumed_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
