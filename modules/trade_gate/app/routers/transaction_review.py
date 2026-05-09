"""取引審査 API ルーター。

ERP 取引伝票・出荷伝票と AI 該非判定を紐づけ、出荷 GO サインを管理する。

エンドポイント:
- GET  /api/transaction-reviews/stats                統計（ダッシュボード用）
- GET  /api/transaction-reviews                      審査一覧
- POST /api/transaction-reviews/check-and-link       ①② ERP 取引伝票受付・審査実行
- GET  /api/transaction-reviews/{id}                 審査詳細
- POST /api/transaction-reviews/{id}/shipment-rescreen  ③ 出荷伝票再スクリーニング
- POST /api/transaction-reviews/{id}/manual-approve  手動承認（NEEDS_REVIEW 解除）
- GET  /api/item-versions/sync-preview               ERP 同期プレビュー（品目同期ボタン用）
- POST /api/item-versions/sync-to-erp                ERP へ品目マスタを一括プッシュ
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..pg_session import get_pg_db
from platform_core.models.company import Company
from platform_core.models.item import Item
from platform_core.models.transaction_review import TransactionReview
from platform_core.models.tenant import Tenant

logger = logging.getLogger(__name__)

router = APIRouter(tags=["transaction_review"])

_SCREENING_URL   = "http://localhost:8005"
_ERP_ADAPTER_URL = "http://localhost:5001"
_REVIEW_VALID_DAYS = 365


# ── Pydantic スキーマ ─────────────────────────────────────────────

class CheckAndLinkRequest(BaseModel):
    erp_transaction_id: str
    item_code: str
    item_name: str
    item_description: str | None = None
    hs_code: str | None = None
    eccn: str | None = None
    counterparty_name: str
    counterparty_country: str
    counterparty_address: str | None = None
    destination_country: str
    quantity: float | None = None
    value_usd: float | None = None


class CheckAndLinkResponse(BaseModel):
    review_id: str
    erp_transaction_id: str
    judgment: str
    review_level: str
    review_completed: bool
    approved: bool
    linked_existing: bool
    eccn: str | None = None
    message: str | None = None


class ShipmentRescreenRequest(BaseModel):
    erp_shipment_id: str


class ShipmentRescreenResponse(BaseModel):
    review_id: str
    erp_shipment_id: str
    approved: bool
    rescreen_changed: bool
    judgment: str
    message: str | None = None


class ManualApproveRequest(BaseModel):
    reviewed_by: str
    notes: str | None = None


# ── シリアライザ ──────────────────────────────────────────────────

def _serialize(r: TransactionReview) -> dict:
    return {
        "id":                  str(r.id),
        "erp_transaction_id":  r.erp_transaction_id,
        "erp_shipment_id":     r.erp_shipment_id,
        "item_id":             str(r.item_id) if r.item_id else None,
        "item_code":           r.item_code,
        "item_name":           r.item_name,
        "company_id":          str(r.company_id) if r.company_id else None,
        "company_name":        r.company_name,
        "destination_country": r.destination_country,
        "eccn":                r.eccn,
        "judgment":            r.judgment,
        "review_level":        r.review_level,
        "review_completed":    r.review_completed,
        "approved":            r.approved,
        "rescreen_changed":    r.rescreen_changed,
        "reviewed_by":         r.reviewed_by,
        "reviewed_at":         r.reviewed_at.isoformat() if r.reviewed_at else None,
        "expires_at":          r.expires_at.isoformat() if r.expires_at else None,
        "notes":               r.notes,
        "created_at":          r.created_at.isoformat() if r.created_at else None,
    }


# ── 外部サービス呼び出し ──────────────────────────────────────────

async def _ai_judge(item_code: str, description: str, hs_code: str | None, eccn: str | None) -> dict:
    if eccn and eccn.upper() != "EAR99":
        return {"judgment": "APPLICABLE", "eccn": eccn, "rationale": f"Known ECCN: {eccn}"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{_ERP_ADAPTER_URL}/gaihi/judge",
                headers={"Authorization": "Bearer dev-erp-integration-key"},
                json={"material_code": item_code, "description": description or item_code, "hs_code": hs_code},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning("_ai_judge failed for %s: %s", item_code, exc)
        return {"judgment": "PENDING", "eccn": None, "rationale": "AI judgment unavailable"}


async def _screen_counterparty(name: str, country: str, address: str | None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_SCREENING_URL}/api/screen",
                json={"company_name": name, "country": country, "address": address, "threshold": 0.75},
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception as exc:
        logger.warning("_screen_counterparty failed for %s: %s", name, exc)
    return {"result_status": "unknown", "matches": []}


# ── 品目・取引先 upsert ───────────────────────────────────────────

async def _ensure_item(
    db: AsyncSession,
    item_code: str,
    item_name: str,
    description: str | None,
    eccn: str | None,
    hs_code: str | None,
    judgment: str | None,
) -> Item:
    item = (await db.execute(select(Item).where(Item.item_code == item_code))).scalar_one_or_none()
    status_map = {"APPLICABLE": "controlled", "NOT_APPLICABLE": "non_controlled", "PENDING": "pending"}
    ctrl_status = status_map.get(judgment or "", "pending")

    if item:
        item.name = item_name
        if description:
            item.description = description
        if eccn:
            item.eccn = eccn
        if hs_code:
            item.hs_code = hs_code
        item.export_control_status = ctrl_status
    else:
        item = Item(
            item_code=item_code,
            name=item_name,
            description=description,
            eccn=eccn,
            hs_code=hs_code,
            export_control_status=ctrl_status,
        )
        db.add(item)
    await db.flush()
    return item


async def _ensure_company(db: AsyncSession, name: str, country: str, address: str | None) -> Company:
    company = (await db.execute(select(Company).where(Company.name == name))).scalar_one_or_none()
    if not company:
        tenant = (await db.execute(select(Tenant).limit(1))).scalar_one_or_none()
        if not tenant:
            raise HTTPException(500, "No tenant found")
        company = Company(
            tenant_id=tenant.id,
            name=name,
            country_code=country[:3] if country else None,
            address=address,
        )
        db.add(company)
        await db.flush()
    return company


# ── エンドポイント ────────────────────────────────────────────────

@router.get("/api/transaction-reviews/stats")
async def get_stats(db: AsyncSession = Depends(get_pg_db)) -> dict:
    total = (await db.execute(func.count(TransactionReview.id))).scalar() or 0
    approved = (await db.execute(
        select(func.count()).where(TransactionReview.approved == True)  # noqa: E712
    )).scalar() or 0
    needs_review = (await db.execute(
        select(func.count()).where(TransactionReview.judgment == "NEEDS_REVIEW")
    )).scalar() or 0
    pending = (await db.execute(
        select(func.count()).where(TransactionReview.review_completed == False)  # noqa: E712
    )).scalar() or 0

    return {
        "total": total,
        "approved": approved,
        "needs_manual_review": needs_review,
        "pending": pending,
        "auto_approved": approved - (total - needs_review - pending),
    }


@router.get("/api/transaction-reviews")
async def list_reviews(
    judgment: str | None = Query(None),
    review_completed: bool | None = Query(None),
    approved: bool | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_pg_db),
) -> list[dict]:
    stmt = select(TransactionReview).order_by(TransactionReview.created_at.desc())
    if judgment:
        stmt = stmt.where(TransactionReview.judgment == judgment)
    if review_completed is not None:
        stmt = stmt.where(TransactionReview.review_completed == review_completed)
    if approved is not None:
        stmt = stmt.where(TransactionReview.approved == approved)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(
            TransactionReview.erp_transaction_id.ilike(like),
            TransactionReview.item_code.ilike(like),
            TransactionReview.company_name.ilike(like),
        ))
    stmt = stmt.limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    return [_serialize(r) for r in rows]


@router.post("/api/transaction-reviews/check-and-link", status_code=201)
async def check_and_link(
    body: CheckAndLinkRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_pg_db),
) -> dict:
    """
    ① 既存審査がある場合: 既存レコードに ERP 取引伝票 ID を紐づけて返す。
    ② 新規の場合: 品目・取引先を登録し、AI 該非判定 + スクリーニングを実行。
    """
    existing = (await db.execute(
        select(TransactionReview).where(
            TransactionReview.item_code == body.item_code,
            TransactionReview.destination_country == body.destination_country.upper(),
            TransactionReview.review_completed == True,  # noqa: E712
            TransactionReview.approved == True,  # noqa: E712
        ).order_by(TransactionReview.created_at.desc()).limit(1)
    )).scalar_one_or_none()

    now = datetime.now(tz=timezone.utc)

    if existing and (existing.expires_at is None or existing.expires_at > now):
        existing.erp_transaction_id = body.erp_transaction_id
        await db.commit()
        return CheckAndLinkResponse(
            review_id=str(existing.id),
            erp_transaction_id=body.erp_transaction_id,
            judgment=existing.judgment,
            review_level=existing.review_level,
            review_completed=existing.review_completed,
            approved=existing.approved,
            linked_existing=True,
            eccn=existing.eccn,
            message="Linked to existing approved review.",
        ).model_dump()

    ai_result = await _ai_judge(
        body.item_code, body.item_description or body.item_name, body.hs_code, body.eccn
    )
    screen_result = await _screen_counterparty(
        body.counterparty_name, body.counterparty_country, body.counterparty_address
    )

    item = await _ensure_item(
        db, body.item_code, body.item_name, body.item_description,
        ai_result.get("eccn") or body.eccn, body.hs_code, ai_result.get("judgment"),
    )
    company = await _ensure_company(db, body.counterparty_name, body.counterparty_country, body.counterparty_address)

    is_sanctioned = screen_result.get("result_status") == "hit"
    ai_judgment = ai_result.get("judgment", "PENDING")

    if is_sanctioned:
        judgment, review_level, approved, review_completed = "REJECTED", "AUTO", False, True
    elif ai_judgment == "APPLICABLE":
        judgment, review_level, approved, review_completed = "NEEDS_REVIEW", "AUTO", False, False
    elif ai_judgment == "NOT_APPLICABLE":
        judgment, review_level, approved, review_completed = "APPROVED", "AUTO", True, True
    else:
        judgment, review_level, approved, review_completed = "NEEDS_REVIEW", "AUTO", False, False

    review = TransactionReview(
        erp_transaction_id=body.erp_transaction_id,
        item_id=item.id,
        item_code=item.item_code,
        item_name=item.name,
        company_id=company.id,
        company_name=company.name,
        destination_country=body.destination_country.upper(),
        eccn=ai_result.get("eccn") or body.eccn,
        judgment=judgment,
        review_level=review_level,
        review_completed=review_completed,
        approved=approved,
        screening_snapshot=screen_result,
        expires_at=now + timedelta(days=_REVIEW_VALID_DAYS) if approved else None,
        notes=ai_result.get("rationale"),
    )
    db.add(review)
    await db.commit()
    await db.refresh(review)

    msg = "New review completed." if review_completed else "Review requires manual confirmation."
    return CheckAndLinkResponse(
        review_id=str(review.id),
        erp_transaction_id=body.erp_transaction_id,
        judgment=judgment,
        review_level=review_level,
        review_completed=review_completed,
        approved=approved,
        linked_existing=False,
        eccn=review.eccn,
        message=msg,
    ).model_dump()


@router.get("/api/transaction-reviews/{review_id}")
async def get_review(review_id: str, db: AsyncSession = Depends(get_pg_db)) -> dict:
    r = await db.get(TransactionReview, uuid.UUID(review_id))
    if not r:
        raise HTTPException(404, "審査レコードが見つかりません")
    return _serialize(r)


@router.post("/api/transaction-reviews/{review_id}/shipment-rescreen")
async def shipment_rescreen(
    review_id: str,
    body: ShipmentRescreenRequest,
    db: AsyncSession = Depends(get_pg_db),
) -> dict:
    """出荷伝票受付: 再スクリーニングし変化があれば NEEDS_REVIEW に戻す。"""
    r = await db.get(TransactionReview, uuid.UUID(review_id))
    if not r:
        raise HTTPException(404, "審査レコードが見つかりません")

    r.erp_shipment_id = body.erp_shipment_id
    rescreen = await _screen_counterparty(r.company_name or "", r.destination_country or "", None)

    prev_hit = (r.screening_snapshot or {}).get("result_status") == "hit"
    new_hit = rescreen.get("result_status") == "hit"
    changed = prev_hit != new_hit

    r.rescreen_result = rescreen
    r.rescreen_changed = changed

    if changed:
        r.judgment = "NEEDS_REVIEW"
        r.review_completed = False
        r.approved = False
        msg = "Re-screening detected changes. Manual review required."
    elif r.judgment == "APPROVED" and not new_hit:
        r.approved = True
        msg = "Re-screening passed. Shipment approved."
    else:
        msg = f"Review status: {r.judgment}. Not yet approved."

    await db.commit()
    return ShipmentRescreenResponse(
        review_id=str(r.id),
        erp_shipment_id=body.erp_shipment_id,
        approved=r.approved,
        rescreen_changed=changed,
        judgment=r.judgment,
        message=msg,
    ).model_dump()


@router.post("/api/transaction-reviews/{review_id}/manual-approve")
async def manual_approve(
    review_id: str,
    body: ManualApproveRequest,
    db: AsyncSession = Depends(get_pg_db),
) -> dict:
    """担当者による手動承認。NEEDS_REVIEW → APPROVED に変更する。"""
    r = await db.get(TransactionReview, uuid.UUID(review_id))
    if not r:
        raise HTTPException(404, "審査レコードが見つかりません")
    if r.judgment == "REJECTED":
        raise HTTPException(400, "REJECTED 審査は手動承認できません")

    now = datetime.now(tz=timezone.utc)
    r.judgment = "APPROVED"
    r.review_level = "MANUAL"
    r.review_completed = True
    r.approved = True
    r.reviewed_by = body.reviewed_by
    r.reviewed_at = now
    r.expires_at = now + timedelta(days=_REVIEW_VALID_DAYS)
    if body.notes:
        r.notes = body.notes
    await db.commit()
    return {"review_id": str(r.id), "approved": True, "reviewed_by": body.reviewed_by}


# ── 品目同期（ERP 連携ボタン用）──────────────────────────────────

@router.get("/api/item-versions/sync-preview")
async def sync_preview(db: AsyncSession = Depends(get_pg_db)) -> dict:
    total = (await db.execute(select(func.count(Item.id)))).scalar() or 0
    with_eccn = (await db.execute(
        select(func.count()).where(Item.eccn != None)  # noqa: E711
    )).scalar() or 0
    items = (await db.execute(
        select(Item).where(Item.eccn != None).limit(20)  # noqa: E711
    )).scalars().all()

    return {
        "total_items": total,
        "items_with_eccn": with_eccn,
        "preview": [
            {"item_code": i.item_code, "name": i.name, "eccn": i.eccn,
             "export_control_status": i.export_control_status}
            for i in items
        ],
    }


@router.post("/api/item-versions/sync-to-erp")
async def sync_to_erp(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_pg_db),
) -> dict:
    items = (await db.execute(
        select(Item).where(Item.eccn != None)  # noqa: E711
    )).scalars().all()

    background_tasks.add_task(_push_items_to_erp, [
        {
            "material_code": i.item_code or str(i.id),
            "new_judgment":  _status_to_judgment(i.export_control_status),
            "new_eccn":      i.eccn,
            "rationale":     f"Sync from AI Trade Management. Name: {i.name}",
        }
        for i in items
    ])

    return {"queued": len(items), "message": f"{len(items)} 件の品目を ERP へ同期キューに追加しました。"}


def _status_to_judgment(status: str | None) -> str:
    return {"controlled": "APPLICABLE", "non_controlled": "NOT_APPLICABLE"}.get(status or "", "PENDING")


async def _push_items_to_erp(items: list[dict]) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        for item in items:
            try:
                await client.post(
                    f"{_ERP_ADAPTER_URL}/internal/push-judgment",
                    headers={"Authorization": "Bearer dev-erp-integration-key"},
                    json=item,
                )
            except Exception as exc:
                logger.warning("push_to_erp failed for %s: %s", item.get("material_code"), exc)
