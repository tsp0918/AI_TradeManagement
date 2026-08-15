import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from platform_core.db.base import PlatformBase


class WebhookEndpoint(PlatformBase):
    """外部システム（CRM/ERP）への Webhook 送信先設定。テナントごとに管理。"""

    __tablename__ = "webhook_endpoint"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("plat_tenant.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    target_system: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # 'crm' | 'erp'
    url: Mapped[str] = mapped_column(Text, nullable=False)
    signing_secret: Mapped[str] = mapped_column(String(256), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    deliveries: Mapped[list["WebhookDelivery"]] = relationship(
        "WebhookDelivery", back_populates="endpoint", cascade="all, delete-orphan"
    )


class WebhookDelivery(PlatformBase):
    """Webhook 配信記録。リトライ状態と DLQ 管理に使用する。"""

    __tablename__ = "webhook_delivery"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    endpoint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("webhook_endpoint.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="pending", nullable=False
    )  # pending / delivered / failed / dlq
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    endpoint: Mapped["WebhookEndpoint"] = relationship(
        "WebhookEndpoint", back_populates="deliveries"
    )
