"""
企業マスタ（共有データ層）

screening / R&D assessment / validation の各モジュールが共通で参照する
取引先企業情報。BIS/OFACスクリーニング結果も保持する。
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from platform_core.db.base import PlatformBase


class Company(PlatformBase):
    """取引先企業マスタ（テナント横断の共有リソース）。"""

    __tablename__ = "plat_company"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plat_tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    name_aliases: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # 別名・略称リスト
    country_code: Mapped[str | None] = mapped_column(String(3), nullable=True)  # ISO 3166-1 alpha-3
    registration_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # スクリーニング結果
    is_sanctioned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sanction_lists: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 例: ["BIS_EL", "OFAC_SDN", "UN_CONSOLIDATED"]
    last_screened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    screening_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # 分類フラグ
    is_end_user: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_consignee: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    end_use_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 与信管理フィールド
    roles: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 例: ["customer", "supplier", "researcher_institution", "consignee", "end_user"]
    credit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0-100
    credit_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 例: {"revenue_usd": 5e8, "employees": 3000, "founded_year": 1985, "industry": "semiconductors",
    #       "data_source": "TDB", "retrieved_at": "2026-01-01", "notes": "..."}
    country_risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0-100
    overall_risk_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"

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
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="companies")
    screening_histories: Mapped[list["CompanyScreeningHistory"]] = relationship(
        "CompanyScreeningHistory", back_populates="company", cascade="all, delete-orphan"
    )
    credit_histories: Mapped[list["CounterpartyCreditHistory"]] = relationship(
        "CounterpartyCreditHistory", back_populates="company", cascade="all, delete-orphan"
    )


class CompanyScreeningHistory(PlatformBase):
    """企業スクリーニング実行履歴。"""

    __tablename__ = "plat_company_screening_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plat_company.id", ondelete="CASCADE"), nullable=False, index=True
    )
    screened_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    lists_checked: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    result_is_hit: Mapped[bool] = mapped_column(Boolean, nullable=False)
    result_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    screened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    company: Mapped["Company"] = relationship("Company", back_populates="screening_histories")


class CounterpartyCreditHistory(PlatformBase):
    """与信スコア変更履歴。"""

    __tablename__ = "plat_counterparty_credit_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plat_company.id", ondelete="CASCADE"), nullable=False, index=True
    )
    changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    previous_credit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_credit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    previous_risk_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    new_risk_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    company: Mapped["Company"] = relationship("Company", back_populates="credit_histories")
