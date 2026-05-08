import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from platform_core.db.base import PlatformBase


class Tenant(PlatformBase):
    """テナント（拠点・組織）。多拠点グローバル管理の基本単位。

    1テナント = 1物理拠点（本社・海外子会社・支店等）。
    parent_id で階層構造を表現し、グループ全体をツリーで管理する。
    """

    __tablename__ = "plat_tenant"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    plan: Mapped[str] = mapped_column(String(50), default="standard", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    settings: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON文字列

    # ── 多拠点管理フィールド ──────────────────────────────────────────
    # 国コード（ISO 3166-1 alpha-2）例: "JP", "US", "DE", "CN"
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    # 主要規制レジーム: "FEFTA"(外為法) / "EAR"(米国) / "EU_DU"(EU) / "MULTIPLE"
    export_regime: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 拠点種別: "HQ"(本社) / "SUBSIDIARY"(子会社) / "BRANCH"(支店) / "AGENT"(代理店)
    org_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # タイムゾーン（IANA）例: "Asia/Tokyo", "America/New_York", "Europe/Berlin"
    timezone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 親拠点 UUID（本社 = NULL、子会社は本社 ID を指す）
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("plat_tenant.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # 表示用フラグ絵文字（例: "🇯🇵", "🇺🇸"）— UI 表示用
    flag_emoji: Mapped[str | None] = mapped_column(String(8), nullable=True)

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
    users: Mapped[list["User"]] = relationship("User", back_populates="tenant")
    companies: Mapped[list["Company"]] = relationship("Company", back_populates="tenant")
    # 子拠点リスト
    children: Mapped[list["Tenant"]] = relationship(
        "Tenant", foreign_keys=[parent_id], back_populates="parent"
    )
    parent: Mapped["Tenant | None"] = relationship(
        "Tenant", foreign_keys=[parent_id], back_populates="children", remote_side=[id]
    )
