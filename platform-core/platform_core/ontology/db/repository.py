"""
platform_core.ontology.db.repository
=======================================
オントロジー永続化層。DBとPydanticモデルの変換を担う。

設計方針:
- 非同期 (AsyncSession) で実装
- Pydantic モデルを直接返す（ORM オブジェクトをサービス層に漏らさない）
- シードデータ (JSON) からのロードもサポート
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from platform_core.ontology.db.schema import HanteiKubanbangORM, HanteiThresholdORM
from platform_core.ontology.models.regulation import (
    ComparisonOperator,
    HanteiKubanbang,
    NonNumericAttribute,
    Threshold,
)

_SEED_FILE = Path(__file__).parent.parent / "seed" / "hantei_kubanbang.json"


# ─────────────────────────────────────────────────
# ORM → Pydantic 変換
# ─────────────────────────────────────────────────

def _orm_to_pydantic(orm: HanteiKubanbangORM) -> HanteiKubanbang:
    thresholds: list[Threshold] = []
    non_numeric: list[NonNumericAttribute] = []

    for t in orm.thresholds:
        if t.threshold_type == "numeric":
            thresholds.append(Threshold(
                attr_key=t.attr_key,
                label=t.label,
                unit=t.unit,
                operator=ComparisonOperator(t.operator or ">="),
                controlled_value=float(t.controlled_value or 0),
                description=t.description,
                priority=t.priority,
                example=t.example,
            ))
        else:
            non_numeric.append(NonNumericAttribute(
                attr_key=t.attr_key,
                label=t.label,
                description=t.description,
                priority=t.priority,
                example=t.example,
                controlled_values=t.controlled_values or [],
                excluded_values=t.excluded_values or [],
            ))

    return HanteiKubanbang(
        item_no=orm.item_no,
        source_type=orm.source_type,
        article_no=orm.article_no,
        eccn=orm.eccn,
        title=orm.title,
        short_label=orm.short_label,
        concern_level=orm.concern_level,
        layer_a_faiss_ids=orm.layer_a_faiss_ids or [],
        thresholds=thresholds,
        non_numeric_attrs=non_numeric,
    )


# ─────────────────────────────────────────────────
# Repository クラス
# ─────────────────────────────────────────────────

class OntologyRepository:
    """非同期DB経由でオントロジーデータにアクセスするリポジトリ"""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def get_all(self) -> list[HanteiKubanbang]:
        result = await self._db.execute(
            select(HanteiKubanbangORM)
            .where(HanteiKubanbangORM.is_active.is_(True))
            .options(selectinload(HanteiKubanbangORM.thresholds))
        )
        return [_orm_to_pydantic(r) for r in result.scalars().all()]

    async def get_by_domain_id(self, domain_id: str) -> Optional[HanteiKubanbang]:
        """domain_id = "FEFTA::7::law::6" or "ECCN::3A001a" """
        if domain_id.startswith("ECCN::"):
            eccn = domain_id.split("::", 1)[1]
            result = await self._db.execute(
                select(HanteiKubanbangORM)
                .where(HanteiKubanbangORM.eccn == eccn, HanteiKubanbangORM.is_active.is_(True))
                .options(selectinload(HanteiKubanbangORM.thresholds))
            )
        else:
            parts = domain_id.split("::")
            item_no, source_type, article_no = (parts[1], parts[2], parts[3]) if len(parts) >= 4 else ("", "", "")
            result = await self._db.execute(
                select(HanteiKubanbangORM)
                .where(
                    HanteiKubanbangORM.item_no == item_no,
                    HanteiKubanbangORM.source_type == source_type,
                    HanteiKubanbangORM.article_no == article_no,
                    HanteiKubanbangORM.is_active.is_(True),
                )
                .options(selectinload(HanteiKubanbangORM.thresholds))
            )
        row = result.scalar_one_or_none()
        return _orm_to_pydantic(row) if row else None


# ─────────────────────────────────────────────────
# シードデータロード（DBなしでも使える）
# ─────────────────────────────────────────────────

def load_seed_ontology(seed_file: Path = _SEED_FILE) -> list[HanteiKubanbang]:
    """
    seed/hantei_kubanbang.json からオントロジーをロードして返す。
    DB未接続時やテスト時に使用する。
    OntologyReasoningEngine はこのリストで初期化できる。
    """
    if not seed_file.exists():
        return []

    raw = json.loads(seed_file.read_text(encoding="utf-8"))
    result: list[HanteiKubanbang] = []
    for item in raw:
        thresholds = [
            Threshold(
                attr_key=t["attr_key"],
                label=t["label"],
                unit=t.get("unit"),
                operator=ComparisonOperator(t.get("operator", ">=")),
                controlled_value=float(t["controlled_value"]),
                description=t.get("description", ""),
                priority=t.get("priority", 50),
                example=t.get("example"),
            )
            for t in item.get("thresholds", [])
        ]
        non_numeric = [
            NonNumericAttribute(
                attr_key=a["attr_key"],
                label=a["label"],
                description=a.get("description", ""),
                priority=a.get("priority", 50),
                example=a.get("example"),
                controlled_values=a.get("controlled_values", []),
                excluded_values=a.get("excluded_values", []),
            )
            for a in item.get("non_numeric_attrs", [])
        ]
        result.append(HanteiKubanbang(
            item_no=item.get("item_no", ""),
            source_type=item.get("source_type", ""),
            article_no=item.get("article_no", ""),
            eccn=item.get("eccn", ""),
            title=item.get("title", ""),
            short_label=item.get("short_label", ""),
            concern_level=item.get("concern_level", "medium"),
            layer_a_faiss_ids=item.get("layer_a_faiss_ids", []),
            thresholds=thresholds,
            non_numeric_attrs=non_numeric,
        ))
    return result
