"""懸念取引先スクリーニング モジュール固有テーブル。

scr_watchlist        : BIS Entity List / OFAC SDN / 独自リストのエントリ
scr_screening_result : 企業ごとのスクリーニング結果詳細
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import ScreeningBase


class Watchlist(ScreeningBase):
    """監視リスト (BIS Entity List / OFAC SDN / 自社リスト) エントリ。"""

    __tablename__ = "scr_watchlist"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # リストのソース識別子: "bis_entity", "ofac_sdn", "internal", etc.
    list_source: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_name: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    aliases: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 住所・国情報
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    # ソース固有の識別子 (BIS EL番号、OFAC ID等)
    source_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    # 制裁理由・備考
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # リスクレベル: "high", "medium", "low"
    risk_level: Mapped[str] = mapped_column(String(20), default="high", nullable=False)
    # 追加情報 (JSONB)
    extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ScreeningResult(ScreeningBase):
    """企業名に対するスクリーニング実行結果。

    platform-core の plat_company_screening_history と連携するが、
    モジュール固有の詳細スコアと一致候補一覧はこちらに保存する。
    """

    __tablename__ = "scr_screening_result"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # platform-core の plat_company.id (外部 FK は意図的に外している)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    # スクリーニング対象の名前・住所 (company_id が無い場合も入力される)
    query_name: Mapped[str] = mapped_column(String(500), nullable=False)
    query_country: Mapped[str | None] = mapped_column(String(10), nullable=True)
    query_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 結果: "clear", "match", "possible_match", "error"
    result_status: Mapped[str] = mapped_column(
        String(30), nullable=False, index=True
    )
    # 最高一致スコア (0.0-1.0)
    max_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 一致候補リスト [{watchlist_id, name, score, source, ...}]
    matches: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # スクリーニング実行ユーザー (platform-core の plat_user.id)
    screened_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    screened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
