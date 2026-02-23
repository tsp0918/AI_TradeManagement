import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from platform_core.db.base import PlatformBase


class ProjectPatentLink(PlatformBase):
    """案件と特許の紐付け。論理削除で履歴を保持する。"""

    __tablename__ = "plat_project_patent_link"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("plat_project.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 特許公開番号 (例: "JP2024-123456A", "US20240123456A1")
    patent_publication_number: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    # メモ / 関連度スコアなど
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 論理削除タイムスタンプ (NULL = 有効, NOT NULL = 削除済み)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships (文字列参照でサーキュラーインポートを回避)
    project: Mapped["Project"] = relationship(  # type: ignore[name-defined]
        "Project",
        back_populates="patent_links",
        foreign_keys="[ProjectPatentLink.project_id]",
    )

    __table_args__ = (
        # 同一案件に同一特許を重複登録できないが、論理削除後は再登録可能
        # PostgreSQL の部分インデックス (deleted_at IS NULL の行のみ対象)
        Index(
            "uq_project_patent_active",
            "project_id",
            "patent_publication_number",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )
