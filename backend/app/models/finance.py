from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin

MONEY = Numeric(18, 4)

"""财务资料中心：原始资料版本化归档 + 月度销售汇总模板 + 不可覆盖交付包。"""


class ArchiveFile(Base, PkMixin, TimestampMixin):
    """原始/系统生成文件元数据。同名禁止静默覆盖 → version 递增。"""

    __tablename__ = "archive_files"
    __table_args__ = (
        UniqueConstraint("company", "period_year", "period_month", "category", "original_name", "version",
                         name="uq_archive_file_version"),
    )

    company: Mapped[str] = mapped_column(String(256), default="浙江柴本网络科技有限公司", index=True)
    category: Mapped[str] = mapped_column(String(32), default="other")
    # bank/jackyun/invoice/sales_summary/other
    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    mime: Mapped[str] = mapped_column(String(128), default="")
    sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    period_year: Mapped[int] = mapped_column(BigInteger, default=0, index=True)
    period_month: Mapped[int] = mapped_column(BigInteger, default=0, index=True)
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    uploader: Mapped[str] = mapped_column(String(64), default="system")
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MonthlyFinancePeriod(Base, PkMixin, TimestampMixin):
    __tablename__ = "monthly_finance_periods"
    __table_args__ = (UniqueConstraint("company", "period_year", "period_month", name="uq_finance_period"),)

    company: Mapped[str] = mapped_column(String(256), default="浙江柴本网络科技有限公司")
    period_year: Mapped[int] = mapped_column(BigInteger, nullable=False)
    period_month: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="INCOMPLETE")  # INCOMPLETE/READY/PACKAGED/SENT/ERROR
    required_types: Mapped[dict] = mapped_column(JSONB, default=dict)
    missing_summary: Mapped[dict] = mapped_column(JSONB, default=dict)


class FinanceSalesReportTemplate(Base, PkMixin, TimestampMixin):
    """每月销售汇总模板；后台任务直接读取，不能依赖浏览器 LocalStorage。"""

    __tablename__ = "finance_sales_report_templates"
    __table_args__ = (
        UniqueConstraint("company", "name", name="uq_finance_sales_report_template_company_name"),
    )

    company: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="默认财务月报")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # [{"key":"platform","label":"平台","enabled":true}, ...]，数组顺序就是 Excel 列顺序。
    fields: Mapped[list] = mapped_column(JSONB, default=list)
    # 业务口径，例如 valid_order_mode / refund_mode / timezone。
    rules: Mapped[dict] = mapped_column(JSONB, default=dict)
    to_addrs: Mapped[list] = mapped_column(JSONB, default=list)
    cc_addrs: Mapped[list] = mapped_column(JSONB, default=list)
    auto_send: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    send_day: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    send_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=10)


class FinanceDeliveryPackage(Base, PkMixin, TimestampMixin):
    __tablename__ = "finance_delivery_packages"
    __table_args__ = (UniqueConstraint("period_id", "version", name="uq_finance_delivery_package_version"),)

    period_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, default=1)  # V1/V2…，V1 发出后不可变
    zip_path: Mapped[str] = mapped_column(Text, default="")
    zip_sha256: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="PACKAGED")  # PACKAGED/SENT/ERROR
    created_at_src: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FinanceDeliveryFile(Base, PkMixin):
    __tablename__ = "finance_delivery_files"

    package_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    archive_file_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)


class EmailDeliveryLog(Base, PkMixin, TimestampMixin):
    """一个账期+版本只允许一条“首次成功发送”；再次发送标记 RESENT。"""

    __tablename__ = "email_delivery_logs"
    __table_args__ = (
        Index(
            "uq_email_delivery_first_sent",
            "package_id",
            unique=True,
            postgresql_where=text("kind = 'first' AND status = 'sent'"),
        ),
    )

    package_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="first")  # first/resent
    to_addrs: Mapped[dict] = mapped_column(JSONB, default=list)
    cc_addrs: Mapped[dict] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(16), default="sent")  # sent/failed
    message_id: Mapped[str] = mapped_column(String(256), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClosingVersion(Base, PkMixin, TimestampMixin):
    __tablename__ = "closing_versions"
    __table_args__ = (UniqueConstraint("period_id", "version", name="uq_closing_version"),)

    period_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
