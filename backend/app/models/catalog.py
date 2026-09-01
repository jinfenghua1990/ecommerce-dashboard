from decimal import Decimal
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin

MONEY = Numeric(18, 4)

"""吉客云是唯一商品/SKU 主档（规格 1.1）：关联主键用吉客云内部 ID / SKU 编码。"""


class Product(Base, PkMixin, TimestampMixin):
    __tablename__ = "products"

    jackyun_goods_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    goods_code: Mapped[str] = mapped_column(String(128), default="", index=True)
    goods_name: Mapped[str] = mapped_column(String(512), default="", index=True)
    category: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(32), default="active")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class ProductSku(Base, PkMixin, TimestampMixin):
    __tablename__ = "product_skus"
    __table_args__ = (UniqueConstraint("jackyun_sku_id", name="uq_sku_jackyun_id"),)

    product_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    jackyun_sku_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    sku_code: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    sku_name: Mapped[str] = mapped_column(String(512), default="", index=True)
    barcode: Mapped[str] = mapped_column(String(128), default="", index=True)
    unit: Mapped[str] = mapped_column(String(32), default="")
    sale_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    default_cost: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class Warehouse(Base, PkMixin, TimestampMixin):
    __tablename__ = "warehouses"

    jackyun_warehouse_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), default="")
    warehouse_type: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="active")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class InventorySnapshot(Base, PkMixin):
    __tablename__ = "inventory_snapshots"

    sku_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    warehouse_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="jackyun")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class SalesChannel(Base, PkMixin, TimestampMixin):
    __tablename__ = "sales_channels"

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), default="")


class Store(Base, PkMixin, TimestampMixin):
    __tablename__ = "stores"

    jackyun_store_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), default="")
    platform: Mapped[str] = mapped_column(String(64), default="", index=True)
    channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
