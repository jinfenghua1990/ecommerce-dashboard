from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin


MONEY = Numeric(18, 4)
QUANTITY = Numeric(18, 4)


class Consumable(Base, PkMixin, TimestampMixin):
    """外包装等耗材主档；code 是用户自定义耗材编码（不自动生成）。

    库存三口径：stock_qty=自有仓、factory_qty=工厂、transit_qty=在途（发往工厂未收货）。
    条形码可与正品相同，系统内以独立 ID 区分，不作唯一键。
    """

    __tablename__ = "consumables"

    code: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), default="", index=True)
    barcode: Mapped[str] = mapped_column(String(128), default="", server_default="", index=True)
    category: Mapped[str] = mapped_column(String(128), default="", index=True)
    unit: Mapped[str] = mapped_column(String(32), default="个")
    purchase_unit_cost: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    purchased_qty: Mapped[Decimal] = mapped_column(QUANTITY, default=Decimal("0"))
    used_qty: Mapped[Decimal] = mapped_column(QUANTITY, default=Decimal("0"))
    stock_qty: Mapped[Decimal] = mapped_column(QUANTITY, default=Decimal("0"))
    factory_qty: Mapped[Decimal] = mapped_column(QUANTITY, default=Decimal("0"))
    transit_qty: Mapped[Decimal] = mapped_column(QUANTITY, default=Decimal("0"))
    min_stock_qty: Mapped[Decimal] = mapped_column(QUANTITY, default=Decimal("0"))
    # 税收分类编码（开票用，19 位；也兼容旧 10 位简称）
    tax_code: Mapped[str] = mapped_column(String(32), default="", server_default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class ConsumableSkuMapping(Base, PkMixin, TimestampMixin):
    """货品每销售/采购一个单位消耗多少包装材料。"""

    __tablename__ = "consumable_sku_mappings"
    __table_args__ = (UniqueConstraint("sku_id", "consumable_id", name="uq_consumable_sku_mapping"),)

    sku_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    consumable_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    usage_per_unit: Mapped[Decimal] = mapped_column(QUANTITY, default=Decimal("1"))
    note: Mapped[str] = mapped_column(Text, default="")


class ConsumableTransaction(Base, PkMixin, TimestampMixin):
    """耗材库存流水；所有库存变化必须留痕。

    transaction_type：purchase=采购入库 / send_factory=发往工厂 / factory_receive=工厂收货 /
    consume=消耗 / stocktake=盘点（含旧 adjustment） / loss=报损 / manual=手工调整。
    location：own=自有仓 / factory=工厂。操作前后库存快照见 *_before/*_after。
    """

    __tablename__ = "consumable_transactions"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "consumable_id", name="uq_consumable_tx_source"),
        UniqueConstraint("request_key", name="uq_consumable_tx_request"),
    )

    consumable_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    request_key: Mapped[str | None] = mapped_column(String(36), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    # 单价 10 位小数：收货快照来自采购明细 unit_cost numeric(18,10)，4 位列会截断（2026-09-07）
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)
    location: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), default="manual")
    source_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    stock_before: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    stock_after: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    factory_before: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    factory_after: Mapped[Decimal | None] = mapped_column(QUANTITY, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class InboundConsumableUsage(Base, PkMixin, TimestampMixin):
    """一张吉客云入库单关联的耗材出库明细。

    该表保存人工确认的业务事实，库存变化同时写入
    ``consumable_transactions``，两者通过 ``link_id`` 幂等关联。
    """

    __tablename__ = "inbound_consumable_usages"
    __table_args__ = (UniqueConstraint("link_id", "consumable_id", name="uq_inbound_consumable_usage"),)

    link_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    inbound_document_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    consumable_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="")
