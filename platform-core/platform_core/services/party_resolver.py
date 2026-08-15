"""Party Resolver サービス — Phase 2。

外部システム（ERP/CRM）から渡された取引先名・外部 ID を
plat_party テーブルに名寄せする。

名寄せスコア閾値:
  >= 0.95  自動マージ（同一 Party とみなす）
  0.85〜0.95  人手確認候補（plat_party_merge_candidate に登録）
  <  0.85  新規 Party 作成
"""
from __future__ import annotations

import difflib
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.models.party import Party, PartyIdentifier, PartyMergeCandidate

logger = logging.getLogger(__name__)

_AUTO_MERGE_THRESHOLD = 0.95
_CANDIDATE_THRESHOLD = 0.85


async def resolve_or_create(
    db: AsyncSession,
    *,
    legal_name: str,
    country_code: Optional[str] = None,
    party_type: Optional[str] = None,
    source_system: str,       # 'crm' | 'erp' | 'aitm'
    external_id: str,
    tenant_id: Optional[uuid.UUID] = None,
) -> Party:
    """外部 ID でまず完全一致検索し、なければ名前類似度で名寄せを試みる。

    Returns:
        既存または新規作成した Party インスタンス（db にフラッシュ済み）
    """
    # ── 1. 外部 ID 完全一致 ────────────────────────────────────────────
    existing_id = await _find_by_external_id(db, source_system, external_id)
    if existing_id:
        party = await db.get(Party, existing_id)
        if party:
            return party

    # ── 2. 同テナント内の全 legal_name を取得して類似度スコア計算 ──────
    stmt = select(Party)
    if tenant_id:
        stmt = stmt.where(Party.tenant_id == tenant_id)
    result = await db.execute(stmt)
    candidates: list[Party] = list(result.scalars().all())

    best_party: Optional[Party] = None
    best_score = 0.0
    for p in candidates:
        score = difflib.SequenceMatcher(None, legal_name.lower(), p.legal_name.lower()).ratio()
        if score > best_score:
            best_score = score
            best_party = p

    # ── 3. スコア判定 ──────────────────────────────────────────────────
    if best_score >= _AUTO_MERGE_THRESHOLD and best_party:
        logger.info(
            "party_resolver: auto-merge '%s' → party_id=%s (score=%.3f)",
            legal_name, best_party.id, best_score,
        )
        await _upsert_identifier(db, best_party.id, source_system, external_id)
        return best_party

    new_party = await _create_party(db, legal_name, country_code, party_type, tenant_id,
                                    source_system, external_id)

    if _CANDIDATE_THRESHOLD <= best_score < _AUTO_MERGE_THRESHOLD and best_party:
        logger.info(
            "party_resolver: merge candidate '%s' vs party_id=%s (score=%.3f)",
            legal_name, best_party.id, best_score,
        )
        await _create_merge_candidate(db, new_party.id, best_party.id, best_score)

    return new_party


# ── 内部ヘルパー ──────────────────────────────────────────────────────────

async def _find_by_external_id(
    db: AsyncSession, system: str, external_id: str
) -> Optional[uuid.UUID]:
    result = await db.execute(
        select(PartyIdentifier.party_id).where(
            PartyIdentifier.system == system,
            PartyIdentifier.external_id == external_id,
        ).limit(1)
    )
    row = result.scalar_one_or_none()
    return row


async def _upsert_identifier(
    db: AsyncSession, party_id: uuid.UUID, system: str, external_id: str
) -> None:
    existing = await db.execute(
        select(PartyIdentifier).where(
            PartyIdentifier.party_id == party_id,
            PartyIdentifier.system == system,
            PartyIdentifier.external_id == external_id,
        ).limit(1)
    )
    if existing.scalar_one_or_none() is None:
        db.add(PartyIdentifier(party_id=party_id, system=system, external_id=external_id))
        await db.flush()


async def _create_party(
    db: AsyncSession,
    legal_name: str,
    country_code: Optional[str],
    party_type: Optional[str],
    tenant_id: Optional[uuid.UUID],
    source_system: str,
    external_id: str,
) -> Party:
    party = Party(
        legal_name=legal_name,
        country_code=country_code,
        party_type=party_type,
        tenant_id=tenant_id,
    )
    db.add(party)
    await db.flush()  # id を確定

    db.add(PartyIdentifier(party_id=party.id, system=source_system, external_id=external_id))
    await db.flush()
    return party


async def _create_merge_candidate(
    db: AsyncSession,
    party_a_id: uuid.UUID,
    party_b_id: uuid.UUID,
    score: float,
) -> None:
    # 重複登録回避
    existing = await db.execute(
        select(PartyMergeCandidate).where(
            PartyMergeCandidate.party_a_id == party_a_id,
            PartyMergeCandidate.party_b_id == party_b_id,
            PartyMergeCandidate.status == "pending",
        ).limit(1)
    )
    if existing.scalar_one_or_none() is None:
        db.add(PartyMergeCandidate(
            party_a_id=party_a_id,
            party_b_id=party_b_id,
            score=score,
        ))
        await db.flush()
