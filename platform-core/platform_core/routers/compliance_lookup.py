"""コンプライアンス進捗管理 Lookup API。

品目を軸に、各コンプライアンスステージの進捗・変化点・未完了アクションを集約する。

エンドポイント:
- GET /api/compliance-lookup/pipeline      品目 × ステージ進捗マトリクス
- GET /api/compliance-lookup/open-actions  未完了アクション一覧（優先度順）
- GET /api/compliance-lookup/change-feed   変化点タイムライン（最新順）
- GET /api/compliance-lookup/stats         サマリー統計
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.db.session import get_db
from platform_core.models.item import Item
from platform_core.models.item_version import ComplianceChangeEvent, ItemVersion
from platform_core.models.export_license import ExportLicenseApplication
from platform_core.models.supply_chain import SupplyChainNode
from platform_core.models.supplier_attestation import SupplierAttestation

router = APIRouter(prefix="/api/compliance-lookup", tags=["compliance_lookup"])

_AI_VALIDATION_URL = "http://localhost:8011"


# ── ステージ定義 ─────────────────────────────────────────────────────────────

STAGES = [
    {"key": "item_registered",  "label": "品目登録",       "icon": "📦"},
    {"key": "hs_classified",    "label": "HS分類",         "icon": "🏷️"},
    {"key": "version_managed",  "label": "バージョン管理", "icon": "🔄"},
    {"key": "validated",        "label": "AI該非判定",     "icon": "🔐"},
    {"key": "screened",         "label": "スクリーニング", "icon": "🛡️"},
    {"key": "supply_chain_ok",  "label": "サプライチェーン", "icon": "🔗"},
    {"key": "license_ready",    "label": "輸出許可",       "icon": "📋"},
]


# ── ai_validation 取引検索 ────────────────────────────────────────────────────

async def _fetch_validation_transactions(item_name: str, eccn: str | None) -> list[dict]:
    """ai_validation から品目名/ECCN でトランザクションを検索する（ベストエフォート）。"""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"{_AI_VALIDATION_URL}/api/transactions/recent",
                params={"limit": 100},
            )
        if resp.status_code != 200:
            return []
        txns = resp.json().get("transactions", [])
        matches = []
        search_terms = [t.lower() for t in (item_name.split() + ([eccn] if eccn else []))]
        for tx in txns:
            title = (tx.get("title") or "").lower()
            if any(term in title for term in search_terms):
                matches.append(tx)
        return matches
    except Exception:
        return []


# ── パイプライン ──────────────────────────────────────────────────────────────

@router.get("/pipeline")
async def get_pipeline(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """品目 × ステージ進捗マトリクスを返す。"""
    items = (await db.execute(
        select(Item).order_by(Item.created_at.desc()).limit(limit).offset(offset)
    )).scalars().all()

    result = []
    for item in items:
        stages: dict[str, Any] = {}

        # Stage 1: 品目登録
        stages["item_registered"] = {"done": True, "detail": item.item_code}

        # Stage 2: HS 分類
        stages["hs_classified"] = {
            "done": bool(item.hs_code),
            "detail": item.hs_code,
        }

        # Stage 3: バージョン管理
        ver_count = await db.scalar(
            select(func.count(ItemVersion.id)).where(ItemVersion.item_id == item.id)
        )
        current_ver = (await db.execute(
            select(ItemVersion)
            .where(ItemVersion.item_id == item.id, ItemVersion.is_current == True)  # noqa: E712
            .limit(1)
        )).scalar_one_or_none()
        open_events = await db.scalar(
            select(func.count(ComplianceChangeEvent.id)).where(
                ComplianceChangeEvent.item_id == item.id,
                ComplianceChangeEvent.status.in_(["open", "in_review"]),
            )
        )
        stages["version_managed"] = {
            "done": (ver_count or 0) > 0,
            "detail": f"v{current_ver.version_number}" if current_ver else None,
            "alert": open_events or 0,
        }

        # Stage 4: AI該非判定（ai_validation HTTP）
        txns = await _fetch_validation_transactions(item.name, item.eccn)
        stages["validated"] = {
            "done": len(txns) > 0,
            "detail": f"{len(txns)}件" if txns else None,
            "link": f"/proxy/ai_validation/ui/transaction/{txns[0]['id']}" if txns else None,
        }

        # Stage 5: スクリーニング（supply chain attestation でサプライヤーが screening 済みかチェック）
        attest_count = await db.scalar(
            select(func.count(SupplierAttestation.id))
            .join(SupplyChainNode, SupplierAttestation.node_id == SupplyChainNode.id)
            .where(SupplierAttestation.status == "accepted")
        )
        stages["screened"] = {
            "done": (attest_count or 0) > 0,
            "detail": f"承認済 {attest_count}件" if attest_count else None,
        }

        # Stage 6: サプライチェーン
        # item_id FK 照合（正式紐付け優先）、未紐付けの場合は名前ベースのフォールバック
        sc_node = (await db.execute(
            select(SupplyChainNode).where(
                SupplyChainNode.item_id == item.id
            ).limit(1)
        )).scalar_one_or_none()
        if sc_node is None:
            sc_node = (await db.execute(
                select(SupplyChainNode).where(
                    SupplyChainNode.name.ilike(f"%{item.name}%")
                ).limit(1)
            )).scalar_one_or_none()
        stages["supply_chain_ok"] = {
            "done": sc_node is not None,
            "detail": sc_node.name if sc_node else None,
            "node_id": str(sc_node.id) if sc_node else None,
            "linked_by_fk": sc_node is not None and sc_node.item_id == item.id if sc_node else False,
        }

        # Stage 7: 輸出許可
        license = (await db.execute(
            select(ExportLicenseApplication)
            .where(
                ExportLicenseApplication.item_description.ilike(f"%{item.name}%"),
                ExportLicenseApplication.status.in_(["approved", "submitted", "draft"]),
            )
            .order_by(ExportLicenseApplication.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        if license is None and item.eccn:
            license = (await db.execute(
                select(ExportLicenseApplication)
                .where(
                    ExportLicenseApplication.eccn == item.eccn,
                    ExportLicenseApplication.status.in_(["approved", "submitted", "draft"]),
                )
                .order_by(ExportLicenseApplication.created_at.desc())
                .limit(1)
            )).scalar_one_or_none()
        stages["license_ready"] = {
            "done": license is not None,
            "detail": license.status if license else None,
            "license_id": str(license.id) if license else None,
        }

        # 全体進捗スコア
        done_count = sum(1 for s in stages.values() if s.get("done"))
        total_stages = len(STAGES)
        progress_pct = round(done_count / total_stages * 100)

        result.append({
            "item": {
                "id": str(item.id),
                "item_code": item.item_code,
                "name": item.name,
                "eccn": item.eccn,
                "hs_code": item.hs_code,
                "export_control_status": item.export_control_status,
            },
            "stages": stages,
            "progress_pct": progress_pct,
            "open_change_events": open_events or 0,
        })

    return {"stages_def": STAGES, "items": result}


# ── オープンアクション ────────────────────────────────────────────────────────

@router.get("/open-actions")
async def get_open_actions(db: AsyncSession = Depends(get_db)):
    """未完了アクション一覧を優先度順に返す。"""
    now = datetime.now(timezone.utc)
    actions: list[dict] = []

    # 1. HIGH コンプライアンス変化点イベント
    high_events = (await db.execute(
        select(ComplianceChangeEvent, Item)
        .join(Item, ComplianceChangeEvent.item_id == Item.id, isouter=True)
        .where(
            ComplianceChangeEvent.impact_level.in_(["HIGH", "MEDIUM"]),
            ComplianceChangeEvent.status.in_(["open", "in_review"]),
        )
        .order_by(
            ComplianceChangeEvent.impact_level,
            ComplianceChangeEvent.created_at.desc(),
        )
        .limit(30)
    )).all()

    for ev, item in high_events:
        action_labels = [a.get("label", "") for a in (ev.action_required or [])]
        actions.append({
            "priority": "urgent" if ev.impact_level == "HIGH" else "high",
            "type": "compliance_event",
            "impact_level": ev.impact_level,
            "category": ev.change_category,
            "title": f"[{ev.impact_level}] {_category_label(ev.change_category)}",
            "item_name": item.name if item else f"item:{str(ev.item_id)[:8]}",
            "item_id": str(ev.item_id),
            "event_id": str(ev.id),
            "actions_needed": action_labels,
            "status": ev.status,
            "created_at": ev.created_at.isoformat() if ev.created_at else None,
        })

    # 2. 期限切れ間近の輸出許可（90日以内）
    soon = now + timedelta(days=90)
    expiring_licenses = (await db.execute(
        select(ExportLicenseApplication)
        .where(
            ExportLicenseApplication.status == "approved",
            ExportLicenseApplication.expires_at != None,  # noqa: E711
            ExportLicenseApplication.expires_at <= soon,
            ExportLicenseApplication.expires_at >= now,
        )
        .order_by(ExportLicenseApplication.expires_at)
        .limit(10)
    )).scalars().all()

    for lic in expiring_licenses:
        days_left = (lic.expires_at - now).days
        actions.append({
            "priority": "urgent" if days_left <= 30 else "high",
            "type": "license_expiry",
            "impact_level": "HIGH" if days_left <= 30 else "MEDIUM",
            "title": f"輸出許可 期限{days_left}日前",
            "item_name": lic.item_description or "—",
            "license_id": str(lic.id),
            "license_number": lic.license_number or "—",
            "actions_needed": ["輸出許可を更新申請する"],
            "status": "expiring",
            "expires_at": lic.expires_at.isoformat() if lic.expires_at else None,
            "created_at": None,
        })

    # 3. 期限切れ間近の原産性証明（60日以内）
    soon_attest = now + timedelta(days=60)
    expiring_attests = (await db.execute(
        select(SupplierAttestation)
        .where(
            SupplierAttestation.status == "accepted",
            SupplierAttestation.expiry_date != None,  # noqa: E711
        )
        .limit(50)
    )).scalars().all()

    for att in expiring_attests:
        if not att.expiry_date:
            continue
        # expiry_date は date 型なので datetime に変換
        exp_dt = datetime.combine(att.expiry_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        days_left = (exp_dt - now).days
        if days_left <= 60:
            actions.append({
                "priority": "high" if days_left <= 30 else "medium",
                "type": "attestation_expiry",
                "impact_level": "MEDIUM",
                "title": f"原産性証明 期限{days_left}日前",
                "item_name": att.supplier_name,
                "attestation_id": str(att.id),
                "actions_needed": ["サプライヤーへ証明更新を依頼"],
                "status": "expiring",
                "expires_at": exp_dt.isoformat(),
                "created_at": None,
            })

    # 4. AI該当判定が未完了の ai_validation トランザクション
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"{_AI_VALIDATION_URL}/api/transactions/recent",
                params={"limit": 50},
            )
        if resp.status_code == 200:
            for tx in resp.json().get("transactions", []):
                if tx.get("status") in ("draft", "pending"):
                    actions.append({
                        "priority": "medium",
                        "type": "validation_pending",
                        "impact_level": "LOW",
                        "title": "AI該非判定 未完了",
                        "item_name": tx.get("title", "—"),
                        "tx_id": tx.get("id"),
                        "case_no": tx.get("case_no"),
                        "actions_needed": ["AI該非判定を完了させる"],
                        "status": tx.get("status"),
                        "created_at": None,
                    })
    except Exception:
        pass

    # 優先度ソート
    _priority_order = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
    actions.sort(key=lambda x: _priority_order.get(x.get("priority", "low"), 99))

    return actions


# ── 変化点タイムライン ─────────────────────────────────────────────────────────

@router.get("/change-feed")
async def get_change_feed(
    limit: int = Query(30, le=100),
    db: AsyncSession = Depends(get_db),
):
    """変化点タイムラインを最新順に返す（品目バージョン + コンプライアンスイベント）。"""
    feed: list[dict] = []

    # ItemVersion 作成
    versions = (await db.execute(
        select(ItemVersion, Item)
        .join(Item, ItemVersion.item_id == Item.id, isouter=True)
        .order_by(ItemVersion.created_at.desc())
        .limit(limit)
    )).all()

    for ver, item in versions:
        feed.append({
            "type": "version_created",
            "icon": "🔄",
            "title": f"v{ver.version_number} 登録{' — ' + ver.version_label if ver.version_label else ''}",
            "item_name": item.name if item else "—",
            "item_id": str(ver.item_id),
            "version_id": str(ver.id),
            "change_category": ver.change_category,
            "source_system": ver.source_system,
            "detail": ver.change_reason,
            "ts": ver.created_at.isoformat() if ver.created_at else None,
        })

    # ComplianceChangeEvent
    events = (await db.execute(
        select(ComplianceChangeEvent, Item)
        .join(Item, ComplianceChangeEvent.item_id == Item.id, isouter=True)
        .order_by(ComplianceChangeEvent.created_at.desc())
        .limit(limit)
    )).all()

    for ev, item in events:
        icon = {"HIGH": "🚨", "MEDIUM": "⚠️", "LOW": "ℹ️", "NONE": "✅"}.get(ev.impact_level, "•")
        feed.append({
            "type": "compliance_event",
            "icon": icon,
            "title": f"[{ev.impact_level}] {_category_label(ev.change_category)}",
            "item_name": item.name if item else "—",
            "item_id": str(ev.item_id),
            "event_id": str(ev.id),
            "status": ev.status,
            "impact_level": ev.impact_level,
            "detail": (ev.impact_details or {}).get("note") or str(ev.impact_details or ""),
            "ts": ev.created_at.isoformat() if ev.created_at else None,
        })

    # 輸出許可申請ステータス変更
    licenses = (await db.execute(
        select(ExportLicenseApplication)
        .order_by(ExportLicenseApplication.updated_at.desc())
        .limit(10)
    )).scalars().all()

    for lic in licenses:
        status_icons = {
            "approved": "✅", "submitted": "📤", "draft": "📝",
            "denied": "❌", "expired": "⌛",
        }
        feed.append({
            "type": "license_updated",
            "icon": status_icons.get(lic.status, "📋"),
            "title": f"輸出許可申請 → {lic.status}",
            "item_name": lic.item_description or lic.eccn or "—",
            "license_id": str(lic.id),
            "detail": lic.license_number or lic.form_type,
            "ts": (lic.updated_at or lic.created_at).isoformat() if (lic.updated_at or lic.created_at) else None,
        })

    # 時刻でソートして上位 limit 件
    feed.sort(key=lambda x: x.get("ts") or "", reverse=True)
    return feed[:limit]


# ── 統計 ─────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    total_items = await db.scalar(select(func.count(Item.id)))
    items_with_hs = await db.scalar(
        select(func.count(Item.id)).where(Item.hs_code != None)  # noqa: E711
    )
    items_with_eccn = await db.scalar(
        select(func.count(Item.id)).where(Item.eccn != None)  # noqa: E711
    )
    open_events = await db.scalar(
        select(func.count(ComplianceChangeEvent.id)).where(
            ComplianceChangeEvent.status.in_(["open", "in_review"])
        )
    )
    high_events = await db.scalar(
        select(func.count(ComplianceChangeEvent.id)).where(
            ComplianceChangeEvent.impact_level == "HIGH",
            ComplianceChangeEvent.status.in_(["open", "in_review"]),
        )
    )
    active_licenses = await db.scalar(
        select(func.count(ExportLicenseApplication.id)).where(
            ExportLicenseApplication.status == "approved"
        )
    )
    pending_licenses = await db.scalar(
        select(func.count(ExportLicenseApplication.id)).where(
            ExportLicenseApplication.status.in_(["draft", "submitted"])
        )
    )
    accepted_attests = await db.scalar(
        select(func.count(SupplierAttestation.id)).where(
            SupplierAttestation.status == "accepted"
        )
    )

    # ai_validation トランザクション数
    val_count = 0
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{_AI_VALIDATION_URL}/api/stats")
        if r.status_code == 200:
            val_count = r.json().get("total_transactions", 0)
    except Exception:
        pass

    return {
        "total_items": total_items or 0,
        "items_with_hs": items_with_hs or 0,
        "items_with_eccn": items_with_eccn or 0,
        "open_change_events": open_events or 0,
        "high_impact_events": high_events or 0,
        "active_licenses": active_licenses or 0,
        "pending_licenses": pending_licenses or 0,
        "accepted_attestations": accepted_attests or 0,
        "validation_transactions": val_count,
    }


# ── ユーティリティ ────────────────────────────────────────────────────────────

def _category_label(cat: str | None) -> str:
    labels = {
        "eccn_change": "ECCN変更",
        "coo_change": "原産国変更",
        "supplier_change": "サプライヤー変更",
        "process_change": "工程変更",
        "composition_change": "組成変更",
        "other": "その他",
    }
    return labels.get(cat or "", cat or "—")
