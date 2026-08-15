"""継続モニタリング購読（Phase 5）。"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.db.base import PlatformBase


class MonitoringSubscription(PlatformBase):
    """監視対象（Party または Transaction）の継続スクリーニング登録。"""

    __tablename__ = "monitoring_subscription"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    subject_type: Mapped[str] = mapped_column(String(20), nullable=False)   # party | transaction
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(30), nullable=False)   # sanction_change | contract_end
    monitor_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    created_from_if: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=True
    )
