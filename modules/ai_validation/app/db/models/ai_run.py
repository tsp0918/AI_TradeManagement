# app/db/models/ai_run.py
from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional, List, Dict, Any, TYPE_CHECKING

from sqlalchemy import String, Integer, DateTime, ForeignKey, Text, Index, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.transaction import Transaction, UsageRequirement
    from app.db.models.patent import Patent
    from app.db.models.matrix import MatrixRule


class RunStatus(str, enum.Enum):
    success = "success"
    failed = "failed"
    running = "running"


class RunType(str, enum.Enum):
    usage_extract = "usage_extract"
    patent_retrieve = "patent_retrieve"
    usage_expand = "usage_expand"
    matrix_match = "matrix_match"
    hs_suggest = "hs_suggest"
    explanation = "explanation"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class AiRun(Base, TimestampMixin):
    __tablename__ = "ai_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id", ondelete="CASCADE"), index=True)

    run_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.running.value, nullable=False)

    model_name: Mapped[Optional[str]] = mapped_column(String(128))
    prompt_version: Mapped[Optional[str]] = mapped_column(String(64))

    params: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    error: Mapped[Optional[str]] = mapped_column(Text)

    transaction: Mapped["Transaction"] = relationship(back_populates="ai_runs")

    patent_retrievals: Mapped[List["PatentRetrieval"]] = relationship(
        back_populates="ai_run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    matrix_matches: Mapped[List["MatrixMatch"]] = relationship(
        back_populates="ai_run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class PatentRetrieval(Base, TimestampMixin):
    __tablename__ = "patent_retrievals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ai_run_id: Mapped[int] = mapped_column(ForeignKey("ai_runs.id", ondelete="CASCADE"), index=True)

    usage_requirement_id: Mapped[int] = mapped_column(ForeignKey("usage_requirements.id", ondelete="CASCADE"), index=True)
    # Layer B 静的インデックス移行後は NULL（publication_number で識別）
    patent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patents.id", ondelete="SET NULL"), index=True, nullable=True)

    # Layer B メタ識別子
    publication_number: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    layer_b_faiss_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    score: Mapped[float] = mapped_column(Float, nullable=False)
    why: Mapped[Optional[str]] = mapped_column(Text)

    ai_run: Mapped["AiRun"] = relationship(back_populates="patent_retrievals")
    usage_requirement: Mapped["UsageRequirement"] = relationship(back_populates="patent_retrievals")
    patent: Mapped[Optional["Patent"]] = relationship(back_populates="retrievals")

    __table_args__ = (Index("ix_patent_retrievals_run_usage", "ai_run_id", "usage_requirement_id"),)


class MatrixMatch(Base):
    __tablename__ = "matrix_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    ai_run_id: Mapped[int] = mapped_column(ForeignKey("ai_runs.id", ondelete="CASCADE"), index=True)
    # Layer A 静的インデックス移行後は NULL（layer_a_faiss_id / layer_a_item_no で識別）
    matrix_rule_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("matrix_rules.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    usage_requirement_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("usage_requirements.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )

    # Layer A メタ識別子
    layer_a_faiss_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    layer_a_item_no: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    layer_a_source_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    match_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # core_hit / expanded_hit
    match_score: Mapped[float] = mapped_column(Float, nullable=False)

    decision: Mapped[str] = mapped_column(String(16), nullable=False, default="hit")

    evidence_json: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    ai_run: Mapped["AiRun"] = relationship(back_populates="matrix_matches")
    usage_requirement: Mapped[Optional["UsageRequirement"]] = relationship(back_populates="matrix_matches")
    matrix_rule: Mapped[Optional["MatrixRule"]] = relationship(back_populates="matches")

    __table_args__ = (Index("ix_matrix_matches_run_rule", "ai_run_id", "matrix_rule_id"),)
