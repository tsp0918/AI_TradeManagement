"""
輸出許可申請・期限管理モデル

EAR BIS-748P（米国多目的申請書）および外為法（様式第1）の
ドラフト生成・申請ステータス追跡・期限アラートを管理する。
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.db.base import PlatformBase


class ExportLicenseApplication(PlatformBase):
    """輸出許可申請。"""

    __tablename__ = "plat_export_license_application"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # 基本情報
    application_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    license_type: Mapped[str] = mapped_column(String(16), nullable=False)  # EAR | FEFTA
    form_type: Mapped[str] = mapped_column(String(32), nullable=False)     # BIS748P | FEFTA_FORM
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    # draft | submitted | approved | denied | expired | not_required

    # 申請品目情報
    item_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    eccn: Mapped[str | None] = mapped_column(String(32), nullable=True)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    value_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 仕向地・需要者
    destination_country: Mapped[str | None] = mapped_column(String(4), nullable=True)
    consignee_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    consignee_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    end_use_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    end_user_name: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # フォームフィールド全体（BIS-748P / 外為法様式 に対応するフラットな JSONB）
    draft_content: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)

    # 紐付け（論理参照）
    transaction_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=list)
    supply_chain_node_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # 許可証情報（承認後に記入）
    license_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    issuing_authority: Mapped[str | None] = mapped_column(String(64), nullable=True)  # BIS | METI
    license_value_remaining_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 日付
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 管理
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    alert_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
