"""采购全链路模型：1688 订单 ↔ 吉客云入库单 / 付款结算单 的关联。"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, PkMixin, TimestampMixin


class ProcurementChainLink(Base, PkMixin, TimestampMixin):
    """1688 订单与入库/付款单据的可审计关联。

    - target_type="inbound"：关联 jackyun_goods_documents.id（吉客云入库单）
    - target_type="settlement"：关联 jackyun_purchase_settlements.id（吉客云采购结算单）
    - 发票与 1688 订单的关联继续走 tax_invoice_links（target_type="alibaba1688_order"）
    """

    __tablename__ = "procurement_chain_links"
    __table_args__ = (
        UniqueConstraint("order_id", "target_type", "target_id", name="uq_procurement_chain_link_target"),
        UniqueConstraint("external_po_id", "target_type", "target_id", name="uq_procurement_workflow_link_target"),
        CheckConstraint("(order_id IS NOT NULL) <> (external_po_id IS NOT NULL)", name="ck_procurement_link_source"),
    )

    order_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("alibaba1688_orders.id"), nullable=True, index=True
    )
    external_po_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("external_purchase_orders.id"), nullable=True, index=True
    )
    target_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # inbound/settlement
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    match_method: Mapped[str] = mapped_column(String(16), default="manual")  # auto/manual/rejected
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)  # 0~1
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    # 入库关联必须明确本次是否产生耗材出库；None/False 均不代表已完成确认。
    consumable_usage_decided: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    consumable_usage_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    order: Mapped["Alibaba1688Order"] = relationship("Alibaba1688Order")

    @property
    def workbench_order_id(self) -> int:
        return self.order_id if self.order_id is not None else -self.external_po_id
