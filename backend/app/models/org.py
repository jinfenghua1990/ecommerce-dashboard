from sqlalchemy import BigInteger, Boolean, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PkMixin, TimestampMixin


class User(Base, PkMixin, TimestampMixin):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Role(Base, PkMixin, TimestampMixin):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), default="")


class UserRole(Base, PkMixin):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_user_role"),)

    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    role_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    # 保留 RBAC 结构；局域网信任模式下不启用（见 IMPLEMENTATION_STATUS）


class AuditLog(Base, PkMixin):
    __tablename__ = "audit_logs"

    actor: Mapped[str] = mapped_column(String(64), default="system", index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), default="", index=True)
    object_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    seq: Mapped[int] = mapped_column(Integer, default=0)
