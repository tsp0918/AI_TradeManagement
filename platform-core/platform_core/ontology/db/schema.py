"""
platform_core.ontology.db.schema
==================================
オントロジー層の SQLAlchemy ORM テーブル定義。

テーブル名プレフィックス: `plat_` (platform_core 規約)

テーブル:
    plat_hantei_kubanbang   - 判定項番マスタ（HanteiKubanbang の永続化）
    plat_hantei_threshold   - 閾値定義（1対多）
    plat_agent_session      - エージェント対話セッション
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey,
    Integer, String, Text, Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from platform_core.db.base import PlatformBase


# ─────────────────────────────────────────────────
# plat_hantei_kubanbang
# ─────────────────────────────────────────────────

class HanteiKubanbangORM(PlatformBase):
    """
    判定項番マスタ。
    Pydantic HanteiKubanbang の永続化。
    外為法 + ECCN の規制項番を統合管理する。
    """
    __tablename__ = "plat_hantei_kubanbang"

    id:           Mapped[int]           = mapped_column(Integer, primary_key=True)
    item_no:      Mapped[str]           = mapped_column(String(32),  nullable=False, default="")
    source_type:  Mapped[str]           = mapped_column(String(32),  nullable=False)
    article_no:   Mapped[str]           = mapped_column(String(32),  nullable=False, default="")
    eccn:         Mapped[str]           = mapped_column(String(32),  nullable=False, default="")
    title:        Mapped[str]           = mapped_column(String(255), nullable=False, default="")
    short_label:  Mapped[str]           = mapped_column(String(128), nullable=False, default="")
    concern_level: Mapped[str]          = mapped_column(String(16),  nullable=False, default="medium")
    layer_a_faiss_ids: Mapped[Optional[list]] = mapped_column(JSON,  nullable=True)
    is_active:    Mapped[bool]          = mapped_column(Boolean,     nullable=False, default=True)
    created_at:   Mapped[datetime]      = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at:   Mapped[datetime]      = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    thresholds: Mapped[list["HanteiThresholdORM"]] = relationship(
        back_populates="kubanbang",
        cascade="all, delete-orphan",
        order_by="HanteiThresholdORM.priority.desc()",
    )


# ─────────────────────────────────────────────────
# plat_hantei_threshold
# ─────────────────────────────────────────────────

class ThresholdType(str, enum.Enum):
    NUMERIC     = "numeric"      # 数値閾値 (Threshold)
    NON_NUMERIC = "non_numeric"  # カテゴリ値 (NonNumericAttribute)


class HanteiThresholdORM(PlatformBase):
    """
    判定閾値定義。
    HanteiKubanbangORM に対して1対多で紐付く。
    Threshold / NonNumericAttribute 両方を収容する。
    """
    __tablename__ = "plat_hantei_threshold"

    id:                Mapped[int]           = mapped_column(Integer, primary_key=True)
    kubanbang_id:      Mapped[int]           = mapped_column(
        Integer, ForeignKey("plat_hantei_kubanbang.id", ondelete="CASCADE"), nullable=False
    )
    threshold_type:    Mapped[str]           = mapped_column(String(16), nullable=False)
    attr_key:          Mapped[str]           = mapped_column(String(64), nullable=False)
    label:             Mapped[str]           = mapped_column(String(128), nullable=False)
    unit:              Mapped[Optional[str]] = mapped_column(String(32),  nullable=True)
    operator:          Mapped[Optional[str]] = mapped_column(String(8),   nullable=True)
    controlled_value:  Mapped[Optional[float]] = mapped_column(Float,     nullable=True)
    controlled_values: Mapped[Optional[list]]  = mapped_column(JSON,      nullable=True)
    excluded_values:   Mapped[Optional[list]]  = mapped_column(JSON,      nullable=True)
    description:       Mapped[str]           = mapped_column(Text,        nullable=False, default="")
    priority:          Mapped[int]           = mapped_column(Integer,     nullable=False, default=50)
    example:           Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    kubanbang: Mapped["HanteiKubanbangORM"] = relationship(back_populates="thresholds")


# ─────────────────────────────────────────────────
# plat_agent_session
# ─────────────────────────────────────────────────

class AgentSessionORM(PlatformBase):
    """
    エージェント対話セッション。
    TransactionContext を DB に永続化し、セッション再開・監査証跡に使う。
    """
    __tablename__ = "plat_agent_session"

    id:               Mapped[int]           = mapped_column(Integer, primary_key=True)
    session_id:       Mapped[str]           = mapped_column(String(64), nullable=False, unique=True, index=True)
    domain:           Mapped[str]           = mapped_column(String(32), nullable=False, default="hantei")
    transaction_id:   Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # TransactionContext の JSON スナップショット
    context_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # 候補項番（カンマ区切り文字列）
    candidate_domain_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status:        Mapped[str]      = mapped_column(String(32), nullable=False, default="active")
    # "active" | "ready_for_judgment" | "judged" | "abandoned"

    turn_count:    Mapped[int]      = mapped_column(Integer, nullable=False, default=0)
    created_at:    Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at:    Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
