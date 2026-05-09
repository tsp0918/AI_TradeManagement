"""サプライヤー原産性証明 API — ai_classification 統合版。

Phase 6A-2: platform-core から移管。データは plat_supplier_attestation テーブル（共有 PostgreSQL）。
"""

import uuid
from datetime import date, datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..pg_session import get_pg_db
from platform_core.models.supplier_attestation import SupplierAttestation
from platform_core.models.supply_chain import SupplyChainNode

router = APIRouter(tags=["supplier_attestation"])


def _eccn_is_controlled(eccn: str | None) -> bool:
    if not eccn:
        return False
    return eccn.upper() not in ("EAR99", "NLR", "N/A", "未判定", "")


def _verdict(claimed: str | None, suggested: str | None, confidence: float) -> str:
    if not suggested:
        return "unverifiable"
    if not claimed or claimed.upper() in ("EAR99", "NLR", ""):
        if _eccn_is_controlled(suggested) and confidence > 0.70:
            return "warning"
        return "match"
    c1 = claimed.upper()[:5]
    c2 = suggested.upper()[:5]
    if c1 == c2:
        return "match"
    if confidence > 0.75:
        return "mismatch"
    return "warning"


def _serialize(a: SupplierAttestation) -> dict:
    return {
        "id": str(a.id),
        "node_id": str(a.node_id),
        "supplier_name": a.supplier_name,
        "supplier_contact": a.supplier_contact,
        "claimed_eccn": a.claimed_eccn,
        "claimed_country_of_origin": a.claimed_country_of_origin,
        "claimed_us_content_pct": a.claimed_us_content_pct,
        "is_us_origin_claimed": a.is_us_origin_claimed,
        "certificate_reference": a.certificate_reference,
        "attestation_date": a.attestation_date.isoformat() if a.attestation_date else None,
        "expiry_date": a.expiry_date.isoformat() if a.expiry_date else None,
        "supporting_docs": a.supporting_docs,
        "notes": a.notes,
        "status": a.status,
        "ai_suggested_eccn": a.ai_suggested_eccn,
        "ai_confidence": a.ai_confidence,
        "ai_verdict": a.ai_verdict,
        "ai_review_detail": a.ai_review_detail,
        "ai_reviewed_at": a.ai_reviewed_at.isoformat() if a.ai_reviewed_at else None,
        "reviewed_by_user_id": str(a.reviewed_by_user_id) if a.reviewed_by_user_id else None,
        "reviewed_at": a.reviewed_at.isoformat() if a.reviewed_at else None,
        "review_comment": a.review_comment,
        "created_at": a.created_at.isoformat(),
        "updated_at": a.updated_at.isoformat(),
    }


# ── スキーマ ──────────────────────────────────────────────────────

class AttestationCreate(BaseModel):
    node_id: str
    supplier_name: str
    supplier_contact: str | None = None
    claimed_eccn: str | None = None
    claimed_country_of_origin: str | None = None
    claimed_us_content_pct: float | None = None
    is_us_origin_claimed: bool = False
    certificate_reference: str | None = None
    attestation_date: str | None = None  # YYYY-MM-DD
    expiry_date: str | None = None
    notes: str | None = None


class AttestationUpdate(BaseModel):
    supplier_name: str | None = None
    supplier_contact: str | None = None
    claimed_eccn: str | None = None
    claimed_country_of_origin: str | None = None
    claimed_us_content_pct: float | None = None
    is_us_origin_claimed: bool | None = None
    certificate_reference: str | None = None
    attestation_date: str | None = None
    expiry_date: str | None = None
    notes: str | None = None


class ReviewBody(BaseModel):
    review_comment: str | None = None


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


# ── ノード別一覧 ───────────────────────────────────────────────────

@router.get("/api/supply-chain/nodes/{node_id}/attestations")
async def list_node_attestations(node_id: str, db: AsyncSession = Depends(get_pg_db)):
    result = await db.execute(
        select(SupplierAttestation)
        .where(SupplierAttestation.node_id == uuid.UUID(node_id))
        .order_by(SupplierAttestation.created_at.desc())
    )
    return [_serialize(a) for a in result.scalars().all()]


# ── 一覧 ─────────────────────────────────────────────────────────

@router.get("/api/supplier-attestations")
async def list_attestations(
    node_id: str | None = Query(None),
    status: str | None = Query(None),
    ai_verdict: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_pg_db),
):
    stmt = select(SupplierAttestation).order_by(SupplierAttestation.created_at.desc())
    items = (await db.execute(stmt)).scalars().all()
    filtered = [
        a for a in items
        if (not node_id or str(a.node_id) == node_id)
        and (not status or a.status == status)
        and (not ai_verdict or a.ai_verdict == ai_verdict)
    ]
    return {"total": len(filtered), "items": [_serialize(a) for a in filtered[offset: offset + limit]]}


# ── 登録 ─────────────────────────────────────────────────────────

@router.post("/api/supplier-attestations", status_code=201)
async def create_attestation(body: AttestationCreate, db: AsyncSession = Depends(get_pg_db)):
    node_id = uuid.UUID(body.node_id)
    r = await db.execute(select(SupplyChainNode).where(SupplyChainNode.id == node_id))
    if r.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="SupplyChainNode not found")

    attest = SupplierAttestation(
        node_id=node_id,
        supplier_name=body.supplier_name,
        supplier_contact=body.supplier_contact,
        claimed_eccn=body.claimed_eccn,
        claimed_country_of_origin=body.claimed_country_of_origin,
        claimed_us_content_pct=body.claimed_us_content_pct,
        is_us_origin_claimed=body.is_us_origin_claimed,
        certificate_reference=body.certificate_reference,
        attestation_date=_parse_date(body.attestation_date),
        expiry_date=_parse_date(body.expiry_date),
        notes=body.notes,
        status="pending",
    )
    db.add(attest)
    await db.commit()
    await db.refresh(attest)
    return _serialize(attest)


# ── 詳細 ─────────────────────────────────────────────────────────

@router.get("/api/supplier-attestations/{attest_id}")
async def get_attestation(attest_id: str, db: AsyncSession = Depends(get_pg_db)):
    r = await db.execute(
        select(SupplierAttestation).where(SupplierAttestation.id == uuid.UUID(attest_id))
    )
    a = r.scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="Not found")
    return _serialize(a)


# ── 更新 ─────────────────────────────────────────────────────────

@router.put("/api/supplier-attestations/{attest_id}")
async def update_attestation(
    attest_id: str, body: AttestationUpdate, db: AsyncSession = Depends(get_pg_db)
):
    r = await db.execute(
        select(SupplierAttestation).where(SupplierAttestation.id == uuid.UUID(attest_id))
    )
    a = r.scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="Not found")
    for f in ("supplier_name", "supplier_contact", "claimed_eccn", "claimed_country_of_origin",
              "claimed_us_content_pct", "is_us_origin_claimed", "certificate_reference", "notes"):
        val = getattr(body, f)
        if val is not None:
            setattr(a, f, val)
    if body.attestation_date is not None:
        a.attestation_date = _parse_date(body.attestation_date)
    if body.expiry_date is not None:
        a.expiry_date = _parse_date(body.expiry_date)
    await db.commit()
    await db.refresh(a)
    return _serialize(a)


# ── 削除 ─────────────────────────────────────────────────────────

@router.delete("/api/supplier-attestations/{attest_id}", status_code=204)
async def delete_attestation(attest_id: str, db: AsyncSession = Depends(get_pg_db)):
    r = await db.execute(
        select(SupplierAttestation).where(SupplierAttestation.id == uuid.UUID(attest_id))
    )
    a = r.scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="Not found")
    await db.delete(a)
    await db.commit()


# ── AI 検証 ──────────────────────────────────────────────────────

@router.post("/api/supplier-attestations/{attest_id}/ai-validate")
async def ai_validate(attest_id: str, db: AsyncSession = Depends(get_pg_db)):
    """FAISS Layer A で申告 ECCN を AI 検証する。"""
    r = await db.execute(
        select(SupplierAttestation).where(SupplierAttestation.id == uuid.UUID(attest_id))
    )
    a = r.scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="Not found")

    rn = await db.execute(select(SupplyChainNode).where(SupplyChainNode.id == a.node_id))
    node = rn.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="SupplyChainNode not found")

    query = node.name
    if node.description:
        query += " " + node.description[:200]
    if a.claimed_eccn and a.claimed_eccn.upper() not in ("EAR99", ""):
        query += f" ECCN {a.claimed_eccn}"

    hits: list[dict[str, Any]] = []
    ai_review_detail: dict[str, Any] = {"query": query}
    try:
        from platform_core.services.faiss_e5_service import is_ready, preload, search_layer_a
        if not is_ready():
            preload(layers=frozenset({"a"}))
        raw_hits = search_layer_a(query, top_k=5)
        for h in raw_hits:
            d = h.__dict__ if hasattr(h, "__dict__") else {}
            hits.append({
                "score": float(d.get("score", 0)),
                "source_type": str(d.get("source_type", "")),
                "source_name": str(d.get("source_name", "")),
                "title": str(d.get("title", "") or d.get("item_label", "")),
                "item_no": str(d.get("item_no", "")),
                "full_text": str(d.get("full_text", "") or d.get("chunk_text", ""))[:300],
            })
        ai_review_detail["hits"] = hits
    except Exception as exc:
        ai_review_detail["error"] = str(exc)

    suggested_eccn: str | None = None
    confidence: float = 0.0
    if hits:
        top = hits[0]
        confidence = top["score"]
        item_no = top.get("item_no", "")
        if item_no and len(item_no) >= 5 and item_no[0].isdigit():
            suggested_eccn = item_no
        elif top.get("source_type") == "eccn":
            parts = top.get("source_name", "").split()
            if parts:
                suggested_eccn = parts[0]

    verdict = _verdict(a.claimed_eccn, suggested_eccn, confidence)
    a.ai_suggested_eccn = suggested_eccn
    a.ai_confidence = round(confidence, 4)
    a.ai_verdict = verdict
    a.ai_review_detail = ai_review_detail
    a.ai_reviewed_at = datetime.now(tz=timezone.utc)
    a.status = "ai_reviewed"
    await db.commit()
    await db.refresh(a)
    return _serialize(a)


# ── 承認 / 却下 ───────────────────────────────────────────────────

@router.post("/api/supplier-attestations/{attest_id}/accept")
async def accept_attestation(attest_id: str, body: ReviewBody, db: AsyncSession = Depends(get_pg_db)):
    r = await db.execute(
        select(SupplierAttestation).where(SupplierAttestation.id == uuid.UUID(attest_id))
    )
    a = r.scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="Not found")
    a.status = "accepted"
    a.reviewed_at = datetime.now(tz=timezone.utc)
    a.review_comment = body.review_comment
    await db.commit()
    await db.refresh(a)
    return _serialize(a)


@router.post("/api/supplier-attestations/{attest_id}/reject")
async def reject_attestation(attest_id: str, body: ReviewBody, db: AsyncSession = Depends(get_pg_db)):
    r = await db.execute(
        select(SupplierAttestation).where(SupplierAttestation.id == uuid.UUID(attest_id))
    )
    a = r.scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="Not found")
    a.status = "rejected"
    a.reviewed_at = datetime.now(tz=timezone.utc)
    a.review_comment = body.review_comment
    await db.commit()
    await db.refresh(a)
    return _serialize(a)
