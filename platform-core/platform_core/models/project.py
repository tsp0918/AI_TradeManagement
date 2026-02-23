import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from platform_core.db.base import PlatformBase


class Project(PlatformBase):
    """案件（プロジェクト）。特許・品目・取引先を束ねる管理単位。"""

    __tablename__ = "plat_project"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # 表示用コード (例: "TS-2024-001") — 任意
    code: Mapped[str | None] = mapped_column(String(50), nullable=True, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # active / archived / merged
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False, index=True)
    # 案件フェーズ (例: "初期調査", "詳細検討", "輸出申請中")
    current_phase: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # マージ先案件ID (status="merged" のときに設定)
    merged_into: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plat_project.id", ondelete="SET NULL"), nullable=True
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

    # Relationships
    patent_links: Mapped[list["ProjectPatentLink"]] = relationship(
        "ProjectPatentLink", back_populates="project", cascade="all, delete-orphan"
    )
    merged_target: Mapped["Project | None"] = relationship(
        "Project", remote_side="Project.id", foreign_keys=[merged_into]
    )
