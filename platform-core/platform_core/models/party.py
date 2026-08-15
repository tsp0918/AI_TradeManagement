"""Party Registry ORM モデル — Phase 2。

ERP / CRM / AI_TM の取引先を統一 ID で管理し、システム間名寄せを行う。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    CHAR, Boolean, DateTime, ForeignKey, Index, Numeric,
    String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from platform_core.db.base import PlatformBase


class Party(PlatformBase):
    __tablename__ = "plat_party"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plat_tenant.id", ondelete="CASCADE"), nullable=True, index=True
    )
    legal_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    country_code: Mapped[Optional[str]] = mapped_column(CHAR(2), nullable=True, index=True)
    party_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)   # company/individual/gov
    risk_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 3), nullable=True)
    sanction_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # clear/possible_match/match
    last_screened_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    identifiers: Mapped[List["PartyIdentifier"]] = relationship(
        "PartyIdentifier", back_populates="party", cascade="all, delete-orphan"
    )
    merge_candidates_as_a: Mapped[List["PartyMergeCandidate"]] = relationship(
        "PartyMergeCandidate", foreign_keys="PartyMergeCandidate.party_a_id", cascade="all, delete-orphan"
    )
    merge_candidates_as_b: Mapped[List["PartyMergeCandidate"]] = relationship(
        "PartyMergeCandidate", foreign_keys="PartyMergeCandidate.party_b_id", cascade="all, delete-orphan"
    )


class PartyIdentifier(PlatformBase):
    __tablename__ = "plat_party_identifier"
    __table_args__ = (
        UniqueConstraint("system", "external_id", name="uq_party_identifier_system_extid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plat_party.id", ondelete="CASCADE"), nullable=False, index=True
    )
    system: Mapped[str] = mapped_column(String(20), nullable=False)      # crm/erp/aitm/duns
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    party: Mapped["Party"] = relationship("Party", back_populates="identifiers")


class PartyMergeCandidate(PlatformBase):
    __tablename__ = "plat_party_merge_candidate"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    party_a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plat_party.id", ondelete="CASCADE"), nullable=False
    )
    party_b_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plat_party.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # pending/merged/rejected
    reviewer_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    party_a: Mapped["Party"] = relationship("Party", foreign_keys=[party_a_id])
    party_b: Mapped["Party"] = relationship("Party", foreign_keys=[party_b_id])
