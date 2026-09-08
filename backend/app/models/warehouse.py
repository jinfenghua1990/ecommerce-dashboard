from __future__ import annotations

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin


class Warehouse(Base, PkMixin, TimestampMixin):
    """本平台仓库主档。

    仓库不写死在业务代码里。当前默认只有 factory / b2c 两类，后续用户可自行
    新增、改名、停用；历史业务单据通过 warehouse_id 保留原仓库引用。
    """

    __tablename__ = "warehouses"

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    warehouse_type: Mapped[str] = mapped_column(String(32), nullable=False, default="other", index=True)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False, default="both")
    is_sellable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
