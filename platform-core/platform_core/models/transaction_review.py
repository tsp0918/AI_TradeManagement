"""取引審査レコード。

ERP の取引伝票・出荷伝票と AI 該非判定結果を紐づけ、
出荷 GO サインの根拠を管理する。
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.db.base import PlatformBase


class TransactionReview(PlatformBase):
    """ERP 取引審査レコード。

    フロー:
      ① 既存審査あり → linked=True で返す
      ② 新規登録     → AUTO 判定後 review_completed=True
      ③ 出荷伝票     → 再スクリーニング後に approved を更新
    """

    __tablename__ = "plat_transaction_review"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # ── ERP 側参照キー ───────────────────────────────────────────────
    erp_transaction_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    erp_shipment_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )

    # ── AI_TM 側マスタ参照 ───────────────────────────────────────────
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    item_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    item_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    company_name: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # ── 判定内容 ─────────────────────────────────────────────────────
    destination_country: Mapped[str | None] = mapped_column(String(10), nullable=True)
    eccn: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # APPROVED / REJECTED / NEEDS_REVIEW / PENDING
    judgment: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)
    # AUTO = AI判定のみ / MANUAL = 担当者確認済
    review_level: Mapped[str] = mapped_column(String(10), default="AUTO", nullable=False)
    review_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # ERP に出荷 OK を返した最終フラグ
    approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ── スクリーニングスナップショット ────────────────────────────────
    screening_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    rescreen_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    rescreen_changed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ── レビュー担当者・有効期限 ─────────────────────────────────────
    reviewed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 審査有効期限（超過後は再審査が必要）
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
