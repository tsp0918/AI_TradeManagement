"""品目バージョン管理 + 仕様変更コンプライアンス影響検知 — ai_classification 統合版。

Phase 6A-2: platform-core から移管。データは plat_item_version / plat_compliance_change_event
テーブル（共有 PostgreSQL）。
"""

import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..pg_session import get_pg_db
from platform_core.models.item import Item
from platform_core.models.item_version import ComplianceChangeEvent, ItemVersion

router = APIRouter(prefix="/api/item-versions", tags=["item_version"])

_VALIDATION_BASE = "http://localhost:8011"

# ── 影響レベルアセスメント ────────────────────────────────────────────────────

_CHANGE_IMPACT: dict[str, str] = {
    "eccn_change": "HIGH",
    "coo_change": "HIGH",
    "composition_change": "HIGH",
    "process_change": "MEDIUM",
    "supplier_change": "MEDIUM",
    "other": "LOW",
}

_CHANGE_ACTIONS: dict[str, list[dict]] = {
    "eccn_change": [
        {"type": "re_validate", "label": "AI該非判定を再実行", "priority": "urgent"},
        {"type": "update_license_drafts", "label": "輸出許可申請ドラフトを更新", "priority": "high"},
    ],
    "coo_change": [
        {"type": "de_minimis_recalc", "label": "De Minimis 比率を再計算", "priority": "high"},
        {"type": "re_attest", "label": "原産性証明を再取得", "priority": "medium"},
    ],
    "composition_change": [
        {"type": "re_validate", "label": "AI該非判定を再実行（組成変更）", "priority": "urgent"},
        {"type": "sds_review", "label": "SDS/GHS 情報を再確認", "priority": "high"},
    ],
    "process_change": [
        {"type": "re_validate", "label": "AI該非判定を再確認（工程変更）", "priority": "high"},
        {"type": "process_review", "label": "製造工程別 ECCN 該当性を確認", "priority": "medium"},
    ],
    "supplier_change": [
        {"type": "re_attest", "label": "新サプライヤーへ原産性証明を依頼", "priority": "high"},
        {"type": "de_minimis_recalc", "label": "De Minimis 比率を再確認", "priority": "medium"},
    ],
    "other": [
        {"type": "manual_review", "label": "コンプライアンス担当者が手動確認", "priority": "low"},
    ],
}


def _assess_diff(prev: ItemVersion | None, curr: ItemVersion) -> list[dict]:
    events: list[dict] = []

    if prev is None:
        cat = curr.change_category or "other"
        events.append({
            "change_category": cat,
            "impact_level": _CHANGE_IMPACT.get(cat, "LOW"),
            "impact_details": {"note": "初回バージョン登録", "category": cat},
            "action_required": _CHANGE_ACTIONS.get(cat, _CHANGE_ACTIONS["other"]),
        })
        return events

    def _diff(field: str) -> tuple[Any, Any] | None:
        old = getattr(prev, field)
        new = getattr(curr, field)
        if old != new:
            return (old, new)
        return None

    if d := _diff("eccn"):
        events.append({
            "change_category": "eccn_change",
            "impact_level": "HIGH",
            "impact_details": {"field": "eccn", "from": d[0], "to": d[1]},
            "action_required": _CHANGE_ACTIONS["eccn_change"],
        })

    if d := _diff("country_of_origin"):
        events.append({
            "change_category": "coo_change",
            "impact_level": "HIGH",
            "impact_details": {"field": "country_of_origin", "from": d[0], "to": d[1]},
            "action_required": _CHANGE_ACTIONS["coo_change"],
        })

    if d := _diff("us_content_pct"):
        old_pct, new_pct = d
        thresholds_crossed = []
        for thr in (10.0, 25.0):
            old_over = (old_pct or 0) >= thr
            new_over = (new_pct or 0) >= thr
            if old_over != new_over:
                thresholds_crossed.append(thr)
        impact = "HIGH" if thresholds_crossed else "MEDIUM"
        events.append({
            "change_category": "coo_change",
            "impact_level": impact,
            "impact_details": {
                "field": "us_content_pct",
                "from": old_pct,
                "to": new_pct,
                "thresholds_crossed": thresholds_crossed,
            },
            "action_required": _CHANGE_ACTIONS["coo_change"],
        })

    if d := _diff("manufacturing_process"):
        events.append({
            "change_category": "process_change",
            "impact_level": "MEDIUM",
            "impact_details": {"field": "manufacturing_process", "changed": True},
            "action_required": _CHANGE_ACTIONS["process_change"],
        })

    if d := _diff("chemical_composition"):
        events.append({
            "change_category": "composition_change",
            "impact_level": "HIGH",
            "impact_details": {"field": "chemical_composition", "changed": True},
            "action_required": _CHANGE_ACTIONS["composition_change"],
        })

    if d := _diff("supplier_ids"):
        old_ids = set(d[0] or [])
        new_ids = set(d[1] or [])
        added = list(new_ids - old_ids)
        removed = list(old_ids - new_ids)
        if added or removed:
            events.append({
                "change_category": "supplier_change",
                "impact_level": "MEDIUM",
                "impact_details": {"added_suppliers": added, "removed_suppliers": removed},
                "action_required": _CHANGE_ACTIONS["supplier_change"],
            })

    explicit_cat = curr.change_category
    if explicit_cat and explicit_cat not in [e["change_category"] for e in events]:
        events.append({
            "change_category": explicit_cat,
            "impact_level": _CHANGE_IMPACT.get(explicit_cat, "LOW"),
            "impact_details": {"note": f"明示的変更カテゴリ: {explicit_cat}"},
            "action_required": _CHANGE_ACTIONS.get(explicit_cat, _CHANGE_ACTIONS["other"]),
        })

    if not events:
        events.append({
            "change_category": "other",
            "impact_level": "NONE",
            "impact_details": {"note": "コンプライアンス上の重要変更なし"},
            "action_required": [],
        })

    return events


# ── シリアライズ ──────────────────────────────────────────────────────────────

def _ser_version(v: ItemVersion) -> dict:
    return {
        "id": str(v.id),
        "item_id": str(v.item_id),
        "version_number": v.version_number,
        "version_label": v.version_label,
        "eccn": v.eccn,
        "hs_code": v.hs_code,
        "country_of_origin": v.country_of_origin,
        "manufacturer": v.manufacturer,
        "us_content_pct": v.us_content_pct,
        "supplier_ids": v.supplier_ids,
        "manufacturing_process": v.manufacturing_process,
        "chemical_composition": v.chemical_composition,
        "spec_snapshot": v.spec_snapshot,
        "change_category": v.change_category,
        "change_reason": v.change_reason,
        "source_system": v.source_system,
        "source_ref": v.source_ref,
        "is_current": v.is_current,
        "created_by_user_id": str(v.created_by_user_id) if v.created_by_user_id else None,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


def _ser_event(e: ComplianceChangeEvent) -> dict:
    return {
        "id": str(e.id),
        "item_id": str(e.item_id),
        "from_version_id": str(e.from_version_id) if e.from_version_id else None,
        "to_version_id": str(e.to_version_id),
        "change_category": e.change_category,
        "impact_level": e.impact_level,
        "impact_details": e.impact_details,
        "action_required": e.action_required,
        "status": e.status,
        "assessed_at": e.assessed_at.isoformat() if e.assessed_at else None,
        "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
        "resolved_by_user_id": str(e.resolved_by_user_id) if e.resolved_by_user_id else None,
        "resolution_notes": e.resolution_notes,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


# ── Pydantic スキーマ ──────────────────────────────────────────────────────

class VersionCreate(BaseModel):
    version_label: str | None = None
    eccn: str | None = None
    hs_code: str | None = None
    country_of_origin: str | None = None
    manufacturer: str | None = None
    us_content_pct: float | None = None
    supplier_ids: list[str] | None = None
    manufacturing_process: str | None = None
    chemical_composition: str | None = None
    spec_snapshot: dict | None = None
    change_category: str | None = None
    change_reason: str | None = None
    source_system: str | None = "manual"
    source_ref: str | None = None


class ItemUpsert(BaseModel):
    item_code: str | None = None
    name: str
    description: str | None = None
    eccn: str | None = None
    hs_code: str | None = None
    export_control_status: str | None = None


class WebhookPayload(BaseModel):
    item_id: str
    source_system: str
    source_ref: str | None = None
    change_category: str | None = None
    change_reason: str | None = None
    version_label: str | None = None
    eccn: str | None = None
    hs_code: str | None = None
    country_of_origin: str | None = None
    manufacturer: str | None = None
    us_content_pct: float | None = None
    supplier_ids: list[str] | None = None
    manufacturing_process: str | None = None
    chemical_composition: str | None = None
    spec_snapshot: dict | None = None


class ResolveBody(BaseModel):
    resolution_notes: str | None = None


# ── 内部ユーティリティ ─────────────────────────────────────────────────────

async def _create_version_and_events(
    db: AsyncSession,
    item_id: uuid.UUID,
    data: VersionCreate | WebhookPayload,
) -> tuple[ItemVersion, list[ComplianceChangeEvent]]:
    prev_result = await db.execute(
        select(ItemVersion)
        .where(ItemVersion.item_id == item_id, ItemVersion.is_current == True)  # noqa: E712
        .order_by(ItemVersion.version_number.desc())
        .limit(1)
    )
    prev = prev_result.scalar_one_or_none()

    if prev is not None:
        await db.execute(
            update(ItemVersion)
            .where(ItemVersion.item_id == item_id, ItemVersion.is_current == True)  # noqa: E712
            .values(is_current=False)
        )
        next_ver_num = prev.version_number + 1
    else:
        next_ver_num = 1

    supplier_ids_val = data.supplier_ids if isinstance(data.supplier_ids, list) else None

    new_ver = ItemVersion(
        id=uuid.uuid4(),
        item_id=item_id,
        version_number=next_ver_num,
        version_label=data.version_label,
        eccn=data.eccn,
        hs_code=data.hs_code,
        country_of_origin=data.country_of_origin,
        manufacturer=data.manufacturer,
        us_content_pct=data.us_content_pct,
        supplier_ids=supplier_ids_val,
        manufacturing_process=data.manufacturing_process,
        chemical_composition=data.chemical_composition,
        spec_snapshot=data.spec_snapshot,
        change_category=data.change_category,
        change_reason=data.change_reason,
        source_system=data.source_system or "manual",
        source_ref=data.source_ref,
        is_current=True,
    )
    db.add(new_ver)
    await db.flush()

    diff_events = _assess_diff(prev, new_ver)
    now = datetime.now(timezone.utc)
    created_events: list[ComplianceChangeEvent] = []

    for ev_data in diff_events:
        ev = ComplianceChangeEvent(
            id=uuid.uuid4(),
            item_id=item_id,
            from_version_id=prev.id if prev else None,
            to_version_id=new_ver.id,
            change_category=ev_data["change_category"],
            impact_level=ev_data["impact_level"],
            impact_details=ev_data["impact_details"],
            action_required=ev_data["action_required"],
            status="open" if ev_data["impact_level"] != "NONE" else "resolved",
            assessed_at=now,
        )
        db.add(ev)
        created_events.append(ev)

    await db.commit()
    await db.refresh(new_ver)
    for ev in created_events:
        await db.refresh(ev)

    return new_ver, created_events


# ── エンドポイント ────────────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_pg_db)):
    total_items_with_versions = await db.scalar(
        select(func.count(func.distinct(ItemVersion.item_id)))
    )
    total_versions = await db.scalar(select(func.count(ItemVersion.id)))
    open_events = await db.scalar(
        select(func.count(ComplianceChangeEvent.id)).where(
            ComplianceChangeEvent.status == "open"
        )
    )
    high_events = await db.scalar(
        select(func.count(ComplianceChangeEvent.id)).where(
            ComplianceChangeEvent.impact_level == "HIGH",
            ComplianceChangeEvent.status.in_(["open", "in_review"]),
        )
    )
    in_review_events = await db.scalar(
        select(func.count(ComplianceChangeEvent.id)).where(
            ComplianceChangeEvent.status == "in_review"
        )
    )
    resolved_events = await db.scalar(
        select(func.count(ComplianceChangeEvent.id)).where(
            ComplianceChangeEvent.status.in_(["resolved", "dismissed"])
        )
    )
    return {
        "items_with_versions": total_items_with_versions or 0,
        "total_versions": total_versions or 0,
        "open_events": open_events or 0,
        "high_impact_events": high_events or 0,
        "in_review_events": in_review_events or 0,
        "resolved_events": resolved_events or 0,
    }


@router.get("/items")
async def list_items(
    q: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_pg_db),
):
    stmt = select(Item)
    if q:
        stmt = stmt.where(Item.name.ilike(f"%{q}%"))
    stmt = stmt.order_by(Item.created_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()

    result = []
    for item in rows:
        ver_row = await db.execute(
            select(ItemVersion)
            .where(ItemVersion.item_id == item.id, ItemVersion.is_current == True)  # noqa: E712
            .limit(1)
        )
        current_ver = ver_row.scalar_one_or_none()
        open_ev_cnt = await db.scalar(
            select(func.count(ComplianceChangeEvent.id)).where(
                ComplianceChangeEvent.item_id == item.id,
                ComplianceChangeEvent.status.in_(["open", "in_review"]),
            )
        )
        result.append({
            "id": str(item.id),
            "item_code": item.item_code,
            "name": item.name,
            "description": item.description,
            "eccn": item.eccn,
            "hs_code": item.hs_code,
            "export_control_status": item.export_control_status,
            "current_version": _ser_version(current_ver) if current_ver else None,
            "open_events": open_ev_cnt or 0,
        })
    return result


@router.post("/items", status_code=201)
async def upsert_item(body: ItemUpsert, db: AsyncSession = Depends(get_pg_db)):
    existing: Item | None = None
    if body.item_code:
        existing = (await db.execute(
            select(Item).where(Item.item_code == body.item_code)
        )).scalar_one_or_none()

    if existing:
        existing.name = body.name
        if body.description is not None:
            existing.description = body.description
        if body.eccn is not None:
            existing.eccn = body.eccn
        if body.hs_code is not None:
            existing.hs_code = body.hs_code
        if body.export_control_status is not None:
            existing.export_control_status = body.export_control_status
        await db.commit()
        await db.refresh(existing)
        return {"id": str(existing.id), "item_code": existing.item_code, "created": False}

    item = Item(
        item_code=body.item_code,
        name=body.name,
        description=body.description,
        eccn=body.eccn,
        hs_code=body.hs_code,
        export_control_status=body.export_control_status or "pending",
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return {"id": str(item.id), "item_code": item.item_code, "created": True}


@router.get("/items/{item_id}")
async def get_item_detail(item_id: uuid.UUID, db: AsyncSession = Depends(get_pg_db)):
    item = await db.get(Item, item_id)
    if not item:
        raise HTTPException(404, "品目が見つかりません")

    versions = (await db.execute(
        select(ItemVersion)
        .where(ItemVersion.item_id == item_id)
        .order_by(ItemVersion.version_number.desc())
    )).scalars().all()

    events = (await db.execute(
        select(ComplianceChangeEvent)
        .where(ComplianceChangeEvent.item_id == item_id)
        .order_by(ComplianceChangeEvent.created_at.desc())
    )).scalars().all()

    return {
        "item": {
            "id": str(item.id),
            "item_code": item.item_code,
            "name": item.name,
            "description": item.description,
            "eccn": item.eccn,
            "hs_code": item.hs_code,
            "export_control_status": item.export_control_status,
            "created_at": item.created_at.isoformat(),
        },
        "versions": [_ser_version(v) for v in versions],
        "events": [_ser_event(e) for e in events],
    }


@router.post("/items/{item_id}/versions", status_code=201)
async def create_version(
    item_id: uuid.UUID, body: VersionCreate, db: AsyncSession = Depends(get_pg_db)
):
    item = await db.get(Item, item_id)
    if not item:
        raise HTTPException(404, "品目が見つかりません")
    new_ver, events = await _create_version_and_events(db, item_id, body)
    return {"version": _ser_version(new_ver), "events_generated": [_ser_event(e) for e in events]}


@router.get("/versions/{version_id}")
async def get_version(version_id: uuid.UUID, db: AsyncSession = Depends(get_pg_db)):
    v = await db.get(ItemVersion, version_id)
    if not v:
        raise HTTPException(404, "バージョンが見つかりません")
    return _ser_version(v)


@router.post("/webhook", status_code=201)
async def receive_webhook(payload: WebhookPayload, db: AsyncSession = Depends(get_pg_db)):
    try:
        item_id = uuid.UUID(payload.item_id)
    except ValueError:
        raise HTTPException(400, "item_id が無効な UUID 形式です")
    item = await db.get(Item, item_id)
    if not item:
        raise HTTPException(404, f"品目 {payload.item_id} が見つかりません")
    new_ver, events = await _create_version_and_events(db, item_id, payload)
    return {
        "received": True,
        "item_id": str(item_id),
        "version": _ser_version(new_ver),
        "events_generated": [_ser_event(e) for e in events],
    }


@router.get("/events")
async def list_events(
    status: str | None = Query(None),
    impact_level: str | None = Query(None),
    change_category: str | None = Query(None),
    item_id: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_pg_db),
):
    stmt = select(ComplianceChangeEvent)
    if status:
        stmt = stmt.where(ComplianceChangeEvent.status == status)
    if impact_level:
        stmt = stmt.where(ComplianceChangeEvent.impact_level == impact_level)
    if change_category:
        stmt = stmt.where(ComplianceChangeEvent.change_category == change_category)
    if item_id:
        try:
            stmt = stmt.where(ComplianceChangeEvent.item_id == uuid.UUID(item_id))
        except ValueError:
            pass
    stmt = stmt.order_by(ComplianceChangeEvent.created_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    return [_ser_event(e) for e in rows]


@router.get("/events/{event_id}")
async def get_event(event_id: uuid.UUID, db: AsyncSession = Depends(get_pg_db)):
    e = await db.get(ComplianceChangeEvent, event_id)
    if not e:
        raise HTTPException(404, "イベントが見つかりません")
    return _ser_event(e)


@router.post("/events/{event_id}/request-validation", status_code=201)
async def request_validation(event_id: uuid.UUID, db: AsyncSession = Depends(get_pg_db)):
    event = await db.get(ComplianceChangeEvent, event_id)
    if not event:
        raise HTTPException(404, "イベントが見つかりません")
    item = await db.get(Item, event.item_id)
    if not item:
        raise HTTPException(404, "品目が見つかりません")
    version = await db.get(ItemVersion, event.to_version_id) if event.to_version_id else None
    eccn_note = f"（ECCN: {version.eccn}）" if version and version.eccn else ""
    details = event.impact_details or {}
    if details.get("from") is not None:
        change_text = f"{details.get('field', '')} 変更: {details.get('from')} → {details.get('to')}"
    else:
        change_text = details.get("note") or event.change_category or "仕様変更"

    payload = {
        "title": f"{item.name}{eccn_note} — 再判定リクエスト",
        "items": [{"item_name": item.name, "item_description": item.description or ""}],
        "usage_requirements": [
            {"source": "item_version", "text": f"仕様変更（{event.change_category}）に伴う再判定。{change_text}"}
        ],
        "source_module": "item_version",
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(f"{_VALIDATION_BASE}/api/transactions", json=payload)
        if r.status_code == 201:
            data = r.json()
            return {
                "ok": True,
                "tx_id": data["id"],
                "case_no": data["case_no"],
                "url": data["url"],
                "redirect_url": f"/proxy/ai_validation/ui/transactions/{data['id']}",
            }
        raise HTTPException(502, f"ai_validation エラー: HTTP {r.status_code}")
    except httpx.RequestError:
        raise HTTPException(503, "ai_validation に接続できません。ポート 8011 を確認してください。")


@router.post("/events/{event_id}/resolve")
async def resolve_event(event_id: uuid.UUID, body: ResolveBody, db: AsyncSession = Depends(get_pg_db)):
    e = await db.get(ComplianceChangeEvent, event_id)
    if not e:
        raise HTTPException(404, "イベントが見つかりません")
    if e.status in ("resolved", "dismissed"):
        raise HTTPException(400, f"既に {e.status} 状態です")
    e.status = "resolved"
    e.resolved_at = datetime.now(timezone.utc)
    e.resolution_notes = body.resolution_notes
    await db.commit()
    await db.refresh(e)
    return _ser_event(e)


@router.post("/events/{event_id}/in-review")
async def set_in_review(event_id: uuid.UUID, db: AsyncSession = Depends(get_pg_db)):
    e = await db.get(ComplianceChangeEvent, event_id)
    if not e:
        raise HTTPException(404, "イベントが見つかりません")
    e.status = "in_review"
    await db.commit()
    await db.refresh(e)
    return _ser_event(e)


@router.post("/events/{event_id}/dismiss")
async def dismiss_event(event_id: uuid.UUID, body: ResolveBody, db: AsyncSession = Depends(get_pg_db)):
    e = await db.get(ComplianceChangeEvent, event_id)
    if not e:
        raise HTTPException(404, "イベントが見つかりません")
    if e.status in ("resolved", "dismissed"):
        raise HTTPException(400, f"既に {e.status} 状態です")
    e.status = "dismissed"
    e.resolved_at = datetime.now(timezone.utc)
    e.resolution_notes = body.resolution_notes
    await db.commit()
    await db.refresh(e)
    return _ser_event(e)
