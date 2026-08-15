"""ライセンスクォータ ORM モデル — Phase 4。

el_license_quota : 政府発行の輸出許可証 1 枚ごとの枠管理
el_license_allocation : 取引ごとの仮引当（行レベルロックでコンカレンシー制御）
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    CHAR, CheckConstraint, Date, DateTime, ForeignKey,
    Integer, Numeric, String, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from platform_core.db.base import PlatformBase


class ExportLicenseQuota(PlatformBase):
    """政府発行の輸出許可証 1 枚ぶんの残枠を管理する。"""
    __tablename__ = "el_license_quota"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    license_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    license_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)   # EAR | FEFTA | individual
    product_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    eccn: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    destination_country: Mapped[Optional[str]] = mapped_column(CHAR(2), nullable=True)
    total_value_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    consumed_value_usd: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    total_unit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    consumed_unit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    valid_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)  # active/expired/revoked
    application_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    allocations: Mapped[List["LicenseAllocation"]] = relationship(
        "LicenseAllocation", back_populates="quota", cascade="all, delete-orphan"
    )

    @property
    def allocated_value_usd(self) -> Decimal:
        """現在有効な仮引当の合計金額。"""
        return sum(
            (a.amount_usd or Decimal("0"))
            for a in self.allocations
            if a.status == "allocated"
        )

    @property
    def available_value_usd(self) -> Optional[Decimal]:
        if self.total_value_usd is None:
            return None
        return self.total_value_usd - self.consumed_value_usd - self.allocated_value_usd

    @property
    def allocated_unit(self) -> int:
        return sum(
            int(a.quantity or 0)
            for a in self.allocations
            if a.status == "allocated"
        )

    @property
    def available_unit(self) -> Optional[int]:
        if self.total_unit is None:
            return None
        return self.total_unit - self.consumed_unit - self.allocated_unit


class LicenseAllocation(PlatformBase):
    """取引ごとの仮引当レコード。"""
    __tablename__ = "el_license_allocation"
    __table_args__ = (
        CheckConstraint("amount_usd > 0 OR amount_usd IS NULL", name="chk_el_alloc_positive_value"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    allocation_no: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    quota_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("el_license_quota.id", ondelete="CASCADE"), nullable=False, index=True
    )
    transaction_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    case_no: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    product_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    quantity: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 3), nullable=True)
    amount_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="allocated", nullable=False)  # allocated/consumed/released/expired
    valid_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    allocated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    quota: Mapped["ExportLicenseQuota"] = relationship("ExportLicenseQuota", back_populates="allocations")
