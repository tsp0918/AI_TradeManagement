# app/db/models/integration.py
from __future__ import annotations

import uuid
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON

from app.db.base import Base  # ← あなたのプロジェクトでBaseのimport先が違う場合は修正


class ExternalEvalRequest(Base):
    """
    UI(Product) → AI判定アプリ への依頼を受け取り、実行・Webhook返却まで追跡するテーブル。
    """
    __tablename__ = "external_eval_requests"

    id = Column(Integer, primary_key=True, index=True)

    # UI側の Product.id
    product_id = Column(Integer, nullable=False, index=True)

    # AI側が発行する request_id（UIへ返す / 監査ログ用）
    request_id = Column(String(64), nullable=False, unique=True, index=True, default=lambda: f"ecreq_{uuid.uuid4().hex}")

    callback_webhook = Column(String(1024), nullable=False)

    # UIから受け取った生payload（JSON文字列）
    payload_in = Column(Text, nullable=False)

    # AI側で作った transaction を追えるとデバッグが楽
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True, index=True)

    status = Column(String(32), nullable=False, default="queued", index=True)  # queued/running/completed/error
    reason = Column(Text, nullable=True)

    # UIへ返す生payload（JSON文字列）
    payload_out = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
