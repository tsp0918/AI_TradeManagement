"""
personnel.py — みなし輸出管理 研究員/従業員エンティティ

外為法施行規則 第17条の3 に基づく「みなし輸出」判定用モデル。
技術提供対象者の国籍・在留年数・二重雇用状況等を管理し、
3カテゴリの該当性を自動判定する。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text

from app.db.base import Base


class Personnel(Base):
    """研究員 / 従業員 / 契約者 エンティティ。"""
    __tablename__ = "personnel"

    personnel_id = Column(
        String(36), primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    # 案件との紐付け（任意 — 案件横断での人物管理も可能）
    case_id = Column(String(64), ForeignKey("rd_cases.case_id", ondelete="SET NULL"),
                     index=True, nullable=True)
    tenant_id = Column(String(64), index=True, nullable=True)

    # ── 個人属性 ─────────────────────────────────────────────────────
    name         = Column(String(200), nullable=False)
    role         = Column(String(100), nullable=True)   # researcher / employee / contractor
    affiliation  = Column(String(200), nullable=True)   # 所属組織・部門

    # ── 国籍・在留情報 ────────────────────────────────────────────────
    nationality       = Column(String(2), nullable=True)   # ISO 3166-1 alpha-2
    residence_country = Column(String(2), nullable=True)   # 現在の居住国
    years_in_japan    = Column(Float, nullable=True)       # 日本在留年数

    # ── 二重雇用・兼職 ───────────────────────────────────────────────
    dual_employment_flag    = Column(Boolean, default=False, nullable=False)
    dual_employer_name      = Column(String(200), nullable=True)
    dual_employer_country   = Column(String(2),   nullable=True)  # ISO 2-letter

    # ── 技術アクセス情報 ──────────────────────────────────────────────
    tech_access_eccn  = Column(String(100), nullable=True)   # アクセス対象 ECCN
    tech_access_fefta = Column(String(100), nullable=True)   # アクセス対象 外為法項番

    # ── みなし輸出判定結果（スクリーニング時に更新）────────────────────
    deemed_export_category = Column(String(1),  nullable=True)   # A / B / C
    deemed_export_risk     = Column(String(20), nullable=True)   # high / medium / low / none
    deemed_export_reason   = Column(Text,       nullable=True)   # 判定理由（JSON list）
    screened_at            = Column(DateTime,   nullable=True)

    # ── 備考 ─────────────────────────────────────────────────────────
    note = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)
