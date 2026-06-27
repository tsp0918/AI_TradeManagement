"""UXイベントログ — プラットフォーム全体の行動シグナル収集。"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.db.base import PlatformBase


class UxEvent(PlatformBase):
    """
    全モジュール横断の UX 行動イベントログ。

    AuditLog (HTTP リクエスト監査) とは別に、
    チュートリアル操作・チャット対話・UI インタラクションなど
    セマンティックな行動シグナルを蓄積する。

    event_type:
        "tutorial"   — チュートリアル操作
        "chat"       — チャットウィジェット対話
        "navigation" — ページ移動
    event_name:
        "tut_step_shown"  / "tut_action_done" / "tut_skipped"
        "tut_question"    / "tut_abandoned"   / "tut_completed"
        "chat_sent"       / "chat_answered"
    """

    __tablename__ = "plat_ux_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    module_key: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    event_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
