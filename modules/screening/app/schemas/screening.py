"""Pydantic スキーマ (スクリーニングリクエスト・レスポンス)。"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# ── リクエスト ──────────────────────────────────────────────────────────────


class ScreenRequest(BaseModel):
    """スクリーニング実行リクエスト。"""

    company_name: str = Field(..., min_length=1, max_length=500, description="照合対象の企業・個人名")
    country: str | None = Field(None, max_length=10, description="ISO 3166-1 alpha-2 国コード")
    address: str | None = Field(None, description="住所 (任意)")
    company_id: uuid.UUID | None = Field(None, description="platform-core の plat_company.id (任意)")
    threshold: float = Field(0.75, ge=0.0, le=1.0, description="一致とみなすスコア閾値")


class WatchlistImportRow(BaseModel):
    """ウォッチリスト一括インポート用の 1 行データ。"""

    list_source: str
    entity_name: str
    aliases: list[str] | None = None
    address: str | None = None
    country: str | None = None
    source_id: str | None = None
    reason: str | None = None
    risk_level: str = "high"
    extra: dict | None = None


# ── レスポンス ──────────────────────────────────────────────────────────────


class MatchDetail(BaseModel):
    """スクリーニング一致候補の詳細。"""

    watchlist_id: uuid.UUID
    entity_name: str
    list_source: str
    score: float
    country: str | None = None
    risk_level: str


class ScreenResultResponse(BaseModel):
    """スクリーニング結果レスポンス。"""

    id: uuid.UUID
    query_name: str
    query_country: str | None
    result_status: str
    max_score: float | None
    matches: list[MatchDetail]
    screened_at: datetime

    model_config = {"from_attributes": True}


class WatchlistEntryResponse(BaseModel):
    """ウォッチリストエントリのレスポンス。"""

    id: uuid.UUID
    list_source: str
    entity_name: str
    aliases: list[str] | None
    country: str | None
    risk_level: str
    is_active: bool

    model_config = {"from_attributes": True}
