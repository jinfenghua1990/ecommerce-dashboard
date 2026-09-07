"""供应链中心：委外生产订单、耗材预占与发厂流转。

生产单代表“补货计划选择工厂生产”后的执行单据。耗材预占只占用可用量，
不直接改动 consumables.stock_qty；实际发往工厂时才通过耗材流水从自有仓转入在途，
工厂签收后再由在途转入工厂库存。每一次发料/签收都保留不可覆盖的 movement 记录。
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
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


class ProductionMaterialMovement(Base, PkMixin, TimestampMixin):
    """生产耗材流转事实。

    dispatch：自有仓 → 在途；factory_receive：在途 → 工厂库存。
    同一个 API request_key 可包含多条耗材，通过 request_key + reservation + type 保证幂等。
    """

    __tablename__ = "production_material_movements"
    __table_args__ = (
        UniqueConstraint(
            "request_key", "reservation_id", "movement_type",
            name="uq_production_material_movement_request",
        ),
        CheckConstraint("quantity > 0", name="ck_production_material_movement_qty"),
        CheckConstraint(
            "movement_type IN ('dispatch', 'factory_receive')",
            name="ck_production_material_movement_type",
        ),
        Index("ix_production_material_movements_order_time", "production_order_id", "occurred_at"),
    )

    movement_no: Mapped[str] = mapped_column(String(48), index=True)
    request_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    production_order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("production_orders.id"), index=True)
    reservation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("production_material_reservations.id"), index=True)
    consumable_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("consumables.id"), index=True)
    movement_type: Mapped[str] = mapped_column(String(24), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    carrier: Mapped[str] = mapped_column(String(128), default="")
    tracking_no: Mapped[str] = mapped_column(String(128), default="", index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(128), default="")
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
