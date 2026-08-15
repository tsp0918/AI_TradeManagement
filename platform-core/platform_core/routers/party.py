"""Party Registry API — Phase 2。

エンドポイント:
  POST /api/parties/resolve         外部 ID → Party 名寄せ（または新規作成）
  GET  /api/parties/{party_id}      Party 詳細取得
  GET  /api/parties/merge-candidates               マージ候補一覧
  POST /api/parties/merge-candidates/{cid}/accept  マージ承認
  POST /api/parties/merge-candidates/{cid}/reject  マージ却下
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.db.session import get_db
from platform_core.models.monitoring import MonitoringSubscription
from platform_core.models.party import Party, PartyIdentifier, PartyMergeCandidate
from platform_core.services.party_resolver import resolve_or_create

router = APIRouter(prefix="/api/parties", tags=["party-registry"])


# ── Pydantic schemas ──────────────────────────────────────────────────────

class PartyResolveRequest(BaseModel):
    legal_name: str
    country_code: Optional[str] = None
    party_type: Optional[str] = None   # company | individual | gov
    source_system: str                 # crm | erp | aitm
    external_id: str
    tenant_id: Optional[uuid.UUID] = None
    enable_watch: bool = False         # True → sanction_change モニタリング購読を作成
    monitor_until: Optional[Any] = None  # YYYY-MM-DD (str) または date オブジェクト


class PartyOut(BaseModel):
    id: uuid.UUID
    legal_name: str
    country_code: Optional[str]
    party_type: Optional[str]
    risk_score: Optional[float]
    sanction_status: Optional[str]
    created_at: str

    @classmethod
    def from_orm(cls, p: Party) -> "PartyOut":
        return cls(
            id=p.id,
            legal_name=p.legal_name,
            country_code=p.country_code,
            party_type=p.party_type,
            risk_score=float(p.risk_score) if p.risk_score is not None else None,
            sanction_status=p.sanction_status,
            created_at=p.created_at.isoformat() if p.created_at else "",
        )


class MergeCandidateOut(BaseModel):
    id: uuid.UUID
    party_a_id: uuid.UUID
    party_b_id: uuid.UUID
    score: float
    status: str
    created_at: str


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.post("/resolve", response_model=dict, status_code=200)
async def resolve_party(
    body: PartyResolveRequest,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """外部システムの取引先を plat_party に名寄せして party_id を返す。"""
    party = await resolve_or_create(
        db,
        legal_name=body.legal_name,
        country_code=body.country_code,
        party_type=body.party_type,
        source_system=body.source_system,
        external_id=body.external_id,
        tenant_id=body.tenant_id,
    )

    subscription_id: Optional[uuid.UUID] = None
    if body.enable_watch:
        from datetime import date as _date
        monitor_until: Optional[_date] = None
        if body.monitor_until:
            if isinstance(body.monitor_until, str):
                monitor_until = _date.fromisoformat(body.monitor_until)
            elif isinstance(body.monitor_until, _date):
                monitor_until = body.monitor_until

        existing_sub = await db.scalar(
            select(MonitoringSubscription).where(
                MonitoringSubscription.subject_type == "party",
                MonitoringSubscription.subject_id == party.id,
                MonitoringSubscription.trigger_type == "sanction_change",
                MonitoringSubscription.is_active.is_(True),
            )
        )
        if not existing_sub:
            sub = MonitoringSubscription(
                subject_type="party",
                subject_id=party.id,
                trigger_type="sanction_change",
                monitor_until=monitor_until,
                created_from_if="IF-01",
            )
            db.add(sub)
            await db.flush()
            subscription_id = sub.id
        else:
            subscription_id = existing_sub.id

    await db.commit()
    return {
        "party_id": str(party.id),
        "legal_name": party.legal_name,
        "subscription_id": str(subscription_id) if subscription_id else None,
    }


@router.get("/merge-candidates", response_model=dict)
async def list_merge_candidates(
    status: str = "pending",
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """マージ候補一覧（デフォルト: status=pending）。"""
    result = await db.execute(
        select(PartyMergeCandidate)
        .where(PartyMergeCandidate.status == status)
        .order_by(PartyMergeCandidate.score.desc())
        .limit(limit)
    )
    candidates = result.scalars().all()
    return {
        "candidates": [
            MergeCandidateOut(
                id=c.id,
                party_a_id=c.party_a_id,
                party_b_id=c.party_b_id,
                score=float(c.score),
                status=c.status,
                created_at=c.created_at.isoformat() if c.created_at else "",
            ).model_dump()
            for c in candidates
        ],
        "total": len(candidates),
    }


@router.get("/{party_id}", response_model=dict)
async def get_party(
    party_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Party 詳細 + 外部 ID リスト。"""
    party = await db.get(Party, party_id)
    if not party:
        raise HTTPException(status_code=404, detail="Party not found")

    id_result = await db.execute(
        select(PartyIdentifier).where(PartyIdentifier.party_id == party_id)
    )
    identifiers = id_result.scalars().all()

    return {
        **PartyOut.from_orm(party).model_dump(),
        "identifiers": [
            {"system": i.system, "external_id": i.external_id, "created_at": i.created_at.isoformat()}
            for i in identifiers
        ],
    }


@router.post("/merge-candidates/{candidate_id}/accept", response_model=dict)
async def accept_merge_candidate(
    candidate_id: uuid.UUID,
    reviewer_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """マージ候補を承認 — party_b の識別子を party_a に移し、party_b を削除。"""
    candidate = await db.get(PartyMergeCandidate, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Merge candidate not found")
    if candidate.status != "pending":
        raise HTTPException(status_code=409, detail=f"Candidate already {candidate.status}")

    # party_b の識別子を party_a に移管
    id_result = await db.execute(
        select(PartyIdentifier).where(PartyIdentifier.party_id == candidate.party_b_id)
    )
    for ident in id_result.scalars().all():
        ident.party_id = candidate.party_a_id

    from datetime import datetime, timezone
    candidate.status = "merged"
    candidate.reviewer_id = reviewer_id
    candidate.reviewed_at = datetime.now(timezone.utc)

    party_b = await db.get(Party, candidate.party_b_id)
    if party_b:
        await db.delete(party_b)

    await db.commit()
    return {"result": "merged", "surviving_party_id": str(candidate.party_a_id)}


@router.post("/merge-candidates/{candidate_id}/reject", response_model=dict)
async def reject_merge_candidate(
    candidate_id: uuid.UUID,
    reviewer_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """マージ候補を却下。"""
    candidate = await db.get(PartyMergeCandidate, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Merge candidate not found")
    if candidate.status != "pending":
        raise HTTPException(status_code=409, detail=f"Candidate already {candidate.status}")

    from datetime import datetime, timezone
    candidate.status = "rejected"
    candidate.reviewer_id = reviewer_id
    candidate.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"result": "rejected", "candidate_id": str(candidate_id)}
