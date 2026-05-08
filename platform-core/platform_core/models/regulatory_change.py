"""
regulatory_change.py — 規制動向モニタリング ログ

Wassenaar / BIS Federal Register / e-Gov 外為法改正 等の
変更検知結果を永続化するテーブル。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB

from platform_core.db.base import PlatformBase


class RegulatoryChange(PlatformBase):
    """検知した規制変更の記録。"""
    __tablename__ = "regulatory_changes"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 変更ソース
    source      = Column(String(50), nullable=False, index=True)
    # "wassenaar" | "bis_federal_register" | "meti_fefta" | "meti_eccn" | "ofac_update"

    # 変更の概要
    change_type = Column(String(50), nullable=True)
    # "addition" | "deletion" | "modification" | "new_rule" | "law_update"

    title       = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)

    # 重要度
    severity    = Column(String(20), default="info", nullable=False)
    # "danger" | "warn" | "info"

    # 対象規制アイテム参照
    item_ref    = Column(String(200), nullable=True)
    # 例: "ECCN 3E001", "外為法別表第一 7項", "EAR Part 744"

    # 有効日
    effective_date = Column(String(20), nullable=True)  # ISO 8601 date string

    # 変更元URL
    source_url  = Column(String(1000), nullable=True)

    # 重複検出用ハッシュ（同一変更を二重登録しない）
    content_hash = Column(String(64), unique=True, nullable=False, index=True)

    # 拠点別フィルタリング（null/[] = 全拠点共通アラート）
    relevant_org_ids = Column(JSONB, nullable=True, default=None)
    # 例: ["uuid1", "uuid2"] → 該当拠点のみに表示

    # 既読フラグ（管理者が確認済みかどうか）
    is_dismissed = Column(Boolean, default=False, nullable=False)
    dismissed_at = Column(DateTime, nullable=True)

    detected_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at   = Column(DateTime, server_default=func.now(), nullable=False)
