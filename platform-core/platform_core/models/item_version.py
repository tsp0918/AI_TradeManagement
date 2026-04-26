"""品目バージョン管理 + 仕様変更コンプライアンス影響検知モデル。"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.db.base import PlatformBase


class ItemVersion(PlatformBase):
    """品目仕様スナップショット（バージョン履歴）。

    外部システム（PLM・SDS/GHS DB）からの変更申請や、手動での仕様変更を記録する。
    is_current=True のレコードが現行バージョン。
    """

    __tablename__ = "plat_item_version"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 任意のラベル例: "Rev.B", "ロットA-2026Q2"
    version_label: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # コンプライアンス上重要なスペック
    eccn: Mapped[str | None] = mapped_column(String(32), nullable=True)
    hs_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    country_of_origin: Mapped[str | None] = mapped_column(String(4), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(512), nullable=True)
    us_content_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 複数サプライヤー・補助スペック（JSONB）
    supplier_ids: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    manufacturing_process: Mapped[str | None] = mapped_column(Text, nullable=True)
    chemical_composition: Mapped[str | None] = mapped_column(Text, nullable=True)
    spec_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # 変更メタ情報
    # "eccn_change" | "coo_change" | "supplier_change" | "process_change"
    # | "composition_change" | "other"
    change_category: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 変更の発信元: "manual" | "webhook_plm" | "webhook_sds" | "webhook_erp"
    source_system: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)

    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ComplianceChangeEvent(PlatformBase):
    """仕様変更によって生じたコンプライアンス影響イベント。

    バージョン作成時に自動アセスメントされ、担当者がレビュー・解決する。
    status フロー: open → in_review → resolved / dismissed
    """

    __tablename__ = "plat_compliance_change_event"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    from_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    to_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )

    change_category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # "HIGH" | "MEDIUM" | "LOW" | "NONE"
    impact_level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    # 変更差分の詳細 {"field": "eccn", "from": "EAR99", "to": "3A001"}
    impact_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 必要なアクション一覧 [{"type": "re_validate", "label": "AI再判定"}, ...]
    action_required: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # "open" | "in_review" | "resolved" | "dismissed"
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="open", index=True
    )
    assessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
