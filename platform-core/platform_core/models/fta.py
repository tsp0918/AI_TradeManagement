"""
EPA/FTA 特恵税率データモデル

plat_fta_agreement  — EPA/FTA 協定マスター
plat_fta_rate       — 品目別特恵税率（HSコード × 協定 × 適用年）
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.db.base import PlatformBase


class FtaAgreement(PlatformBase):
    """EPA/FTA 協定マスター。"""

    __tablename__ = "plat_fta_agreement"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    name_ja: Mapped[str] = mapped_column(String(200), nullable=False)
    name_en: Mapped[str] = mapped_column(String(200), nullable=False)
    partner_countries: Mapped[str] = mapped_column(Text, nullable=False)  # カンマ区切り ISO2
    effective_date: Mapped[str | None] = mapped_column(String(20), nullable=True)  # YYYY-MM-DD
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    # "active" | "provisional" | "suspended"
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class FtaRate(PlatformBase):
    """EPA/FTA 品目別特恵税率。"""

    __tablename__ = "plat_fta_rate"
    __table_args__ = (
        UniqueConstraint("agreement_code", "hs_code", "year", name="uq_fta_rate"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agreement_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    hs_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    hs_description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    mfn_rate_pct: Mapped[float | None] = mapped_column(Float, nullable=True)   # MFN 税率 (%)
    preferential_rate_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # 特恵税率 (%)
    staging_category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # A=即時撤廃, B=段階的, C=除外, D=特殊, TRQ=関税割当
    is_eliminated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    elimination_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
