import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.db.base import PlatformBase


class Item(PlatformBase):
    """品目（輸出管理対象品）。該非判定・特許検索の対象となる製品・技術。"""

    __tablename__ = "plat_item"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # 社内品番 (例: "PROD-2024-001") — 任意
    item_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True, unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # HSコード (例: "8542.31")
    hs_code: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    # ECCN (Export Control Classification Number, 例: "EAR99", "3E001")
    eccn: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    # 該非判定結果 (例: "non_controlled", "controlled", "pending")
    export_control_status: Mapped[str] = mapped_column(
        String(50), default="pending", nullable=False
    )
    # tenant_id: 将来のマルチテナント対応用 (現在は単一組織のため未使用)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
