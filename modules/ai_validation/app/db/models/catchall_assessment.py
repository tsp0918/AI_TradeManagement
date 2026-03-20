"""
CatchallAssessment DB model — キャッチオール自己判定結果の永続化
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


class CatchallAssessment(Base):
    """キャッチオール規制（輸出令第4条・第4条の2）自己判定結果を保存するテーブル。"""

    __tablename__ = "catchall_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # ── 取引紐付け ────────────────────────────────────────────────────────────
    transaction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[str] = mapped_column(String(128), nullable=False)

    # ── 総合判定 ──────────────────────────────────────────────────────────────
    # verdict: "requires_permit" | "caution" | "clear" | "indeterminate"
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    verdict_label: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    verdict_detail: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── 仕向地情報 ────────────────────────────────────────────────────────────
    destination_country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    destination_risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_concern_country: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)

    # ── Red Flag サマリー ─────────────────────────────────────────────────────
    red_flag_positive_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── 判定全体 JSON（CatchallJudgment.to_dict() の結果を丸ごと保存）────────
    judgment_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # ── タイムスタンプ ────────────────────────────────────────────────────────
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
