"""
Transaction model
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional, List, Dict, Any, TYPE_CHECKING

from sqlalchemy import String, Integer, DateTime, ForeignKey, Text, Index, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base


if TYPE_CHECKING:
    from app.db.models.ai_run import PatentRetrieval


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class Patent(Base, TimestampMixin):
    __tablename__ = "patents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    publication_number: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(512))
    assignee: Mapped[Optional[str]] = mapped_column(String(512))
    abstract: Mapped[Optional[str]] = mapped_column(Text)
    fulltext: Mapped[Optional[str]] = mapped_column(Text)

    ipc_codes_raw: Mapped[Optional[str]] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(String(1024))
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    usecases: Mapped[List["PatentUsecase"]] = relationship(
        back_populates="patent",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    retrievals: Mapped[List["PatentRetrieval"]] = relationship(
        back_populates="patent",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class PatentUsecase(Base, TimestampMixin):
    __tablename__ = "patent_usecases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patent_id: Mapped[int] = mapped_column(ForeignKey("patents.id", ondelete="CASCADE"), index=True)

    usecase_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_usecase_text: Mapped[Optional[str]] = mapped_column(Text)

    extraction_method: Mapped[str] = mapped_column(String(32), default="llm", nullable=False)
    quality_score: Mapped[Optional[float]] = mapped_column(Float)

    patent: Mapped["Patent"] = relationship(back_populates="usecases")

    __table_args__ = (Index("ix_patent_usecases_patent", "patent_id"),)
