import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from platform_core.db.base import PlatformBase


class UserRole(str, Enum):
    SUPERADMIN = "superadmin"   # プラットフォーム管理者
    TENANT_ADMIN = "tenant_admin"  # テナント管理者
    MEMBER = "member"           # 一般ユーザー
    READONLY = "readonly"       # 閲覧専用


class AuthProvider(str, Enum):
    LOCAL = "local"
    GOOGLE = "google"
    MICROSOFT = "microsoft"


class User(PlatformBase):
    """プラットフォームユーザー。テナントに紐づく。"""

    __tablename__ = "plat_user"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plat_tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(50), default=UserRole.MEMBER, nullable=False)
    auth_provider: Mapped[str] = mapped_column(
        String(50), default=AuthProvider.LOCAL, nullable=False
    )
    # OAuth2 SSOで取得したプロバイダー側のID
    oauth_sub: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="users")
    module_permissions: Mapped[list["UserModulePermission"]] = relationship(
        "UserModulePermission", back_populates="user", cascade="all, delete-orphan"
    )


class UserModulePermission(PlatformBase):
    """ユーザーごとの各モジュールへのアクセス権限。"""

    __tablename__ = "plat_user_module_permission"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plat_user.id", ondelete="CASCADE"), nullable=False
    )
    module_key: Mapped[str] = mapped_column(String(100), nullable=False)
    can_read: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_write: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="module_permissions")
