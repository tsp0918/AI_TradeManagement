"""
サプライヤーポータル招待トークン

コンプライアンス担当者がサプライヤーに発行する使い捨て（または複数回利用可能な）
招待 URL のトークンを管理する。
サプライヤーはログイン不要でトークン URL にアクセスし、ECCN・原産地を申告できる。
"""

import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from platform_core.db.base import PlatformBase


def _default_expires() -> datetime:
    from datetime import timezone
    return datetime.now(tz=timezone.utc) + timedelta(days=30)


class SupplierPortalToken(PlatformBase):
    """サプライヤーポータル招待トークン。"""

    __tablename__ = "plat_supplier_portal_token"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # トークン文字列（URL に含まれる）— secrets モジュールで生成
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # 対象 BOM ノード（外部キーは持たず論理参照）
    node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    node_name: Mapped[str] = mapped_column(String(512), nullable=False)  # 発行時点のスナップショット

    # サプライヤー情報（フォームに事前表示）
    supplier_name: Mapped[str] = mapped_column(String(512), nullable=False)
    supplier_email: Mapped[str | None] = mapped_column(String(512), nullable=True)
    note_for_supplier: Mapped[str | None] = mapped_column(Text, nullable=True)  # 担当者からの備考

    # アクセス制御
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 0 = 無制限
    use_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_default_expires
    )

    # 発行者
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
