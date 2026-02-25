"""Pydantic スキーマ定義。"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl


class ClassifyRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    item_id: str
    item_name: str
    item_description: str
    additional_keywords: list[str] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=1, le=20)
    callback_url: str   # HttpUrl は str として受け取る


class ClassifyAccepted(BaseModel):
    request_id: str
    status: str = "accepted"
    message: str = "Classification task queued"


class HSCandidate(BaseModel):
    rank: int
    hs_code: str
    description_en: str
    section: str
    chapter: str
    chapter_description: str | None = None
    heading: str
    heading_description: str | None = None
    atlas_short_name: str | None = None
    similarity_score: float
    hs2017_codes: list[str] = Field(default_factory=list)


class ModelInfo(BaseModel):
    embedding_model: str
    dataset_version: str
    index_size: int


class ClassifyResult(BaseModel):
    """webhook 送信ペイロード。"""
    request_id: str
    item_id: str
    status: str                            # "completed" | "failed"
    classified_at: datetime | None = None
    results: list[HSCandidate] = Field(default_factory=list)
    model_info: ModelInfo | None = None
    error: str | None = None
    failed_at: datetime | None = None


class SearchResult(BaseModel):
    query: str
    results: list[HSCandidate]
    index_size: int


class IndexStatus(BaseModel):
    built: bool
    index_size: int
    model: str
    cache_exists: bool
    hs_data_exists: bool
