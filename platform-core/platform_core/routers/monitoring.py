"""継続モニタリング管理 API（Phase 5）。

エンドポイント:
  POST   /api/monitoring/subscriptions          購読を作成（または既存を返す）
  GET    /api/monitoring/subscriptions          購読一覧
  DELETE /api/monitoring/subscriptions/{id}     購読を非アクティブ化
  POST   /api/monitoring/run-now               監視バッチを即時実行（IF-11 疎通確認用）
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.db.session import AsyncSessionLocal, get_db
from platform_core.models.monitoring import MonitoringSubscription
from platform_core.models.party import Party
from platform_core.services.webhook import WebhookDispatcher

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])
_logger = logging.getLogger(__name__)

_OUTCOME_BY_STATUS = {
    "match": "hit",
    "possible_match": "needs_review",
    "clear": "clear",
}


async def run_monitoring_once() -> dict:
    """アクティブな監視購読を全件処理する（バックグラウンドワーカーと共有）。

    Returns:
        {"checked": n, "changed": n, "expired_deactivated": n}
    """
    screening_url = os.environ.get("MODULE_SCREENING_URL", "http://localhost:8005")
    today = date.today()
    expired_count = 0
    checked = 0
    changed = 0

    # Step 1: 期限切れ購読を非アクティブ化
    async with AsyncSessionLocal() as db:
        expired_result = await db.execute(
            select(MonitoringSubscription).where(
                MonitoringSubscription.is_active.is_(True),
                MonitoringSubscription.monitor_until < today,
            )
        )
        expired_subs = expired_result.scalars().all()
        if expired_subs:
            for s in expired_subs:
                s.is_active = False
            await db.commit()
            expired_count = len(expired_subs)
            _logger.info("MonitoringWorker: deactivated %d expired subscriptions", expired_count)

    # Step 2: party / sanction_change 購読を再スクリーニング
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(MonitoringSubscription).where(
                MonitoringSubscription.is_active.is_(True),
                MonitoringSubscription.subject_type == "party",
                MonitoringSubscription.trigger_type == "sanction_change",
            )
        )
        active_subs = result.scalars().all()
        for sub in active_subs:
            party = await db.get(Party, sub.subject_id)
            if not party:
                continue
            checked += 1
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        f"{screening_url}/api/screen",
                        json={"company_name": party.legal_name, "country": party.country_code or None},
                    )
                if resp.status_code != 200:
                    _logger.warning(
                        "MonitoringWorker: screening returned %s for party=%s: %s",
                        resp.status_code, party.id, resp.text[:200],
                    )
                    continue
                new_status = resp.json().get("result_status", "clear")
            except Exception as exc:
                _logger.debug("MonitoringWorker: screening call failed for %s: %s", party.id, exc)
                continue

            prev_status = party.sanction_status or "clear"
            if new_status != prev_status:
                party.sanction_status = new_status
                party.last_screened_at = datetime.now(timezone.utc)
                await db.flush()
                try:
                    await WebhookDispatcher.enqueue(
                        db,
                        tenant_id=party.tenant_id,
                        event_type="screening.alert",
                        payload={
                            "event_id": f"party-alert:{party.id}:{new_status}",
                            "event_type": "screening.alert",
                            "aitm_party_id": str(party.id),
                            "check_type": "sanctions",
                            "outcome": _OUTCOME_BY_STATUS.get(new_status, "unknown"),
                            "detail": {
                                "prev_status": prev_status,
                                "new_status": new_status,
                                "legal_name": party.legal_name,
                                "checked_at": datetime.now(timezone.utc).isoformat(),
                            },
                        },
                    )
                except Exception as exc:
                    _logger.warning("MonitoringWorker: webhook enqueue failed: %s", exc)
                changed += 1

        if changed:
            await db.commit()
        _logger.info(
            "MonitoringWorker: checked=%d changed=%d expired_deactivated=%d",
            checked, changed, expired_count,
        )

    return {"checked": checked, "changed": changed, "expired_deactivated": expired_count}


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class SubscriptionCreateRequest(BaseModel):
    subject_type: str          # 'party' | 'transaction'
    subject_id: uuid.UUID
    trigger_type: str          # 'sanction_change' | 'contract_end'
    monitor_until: Optional[date] = None
    created_from_if: Optional[str] = None  # 'IF-01' | 'IF-02'


def _sub_to_dict(s: MonitoringSubscription) -> dict:
    return {
        "id": str(s.id),
        "subject_type": s.subject_type,
        "subject_id": str(s.subject_id),
        "trigger_type": s.trigger_type,
        "monitor_until": s.monitor_until.isoformat() if s.monitor_until else None,
        "is_active": s.is_active,
        "created_from_if": s.created_from_if,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/subscriptions", response_model=dict, status_code=201)
async def create_subscription(
    body: SubscriptionCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """モニタリング購読を作成する。同一対象×トリガー種別がアクティブで既に存在する場合は既存を返す。"""
    # 既存アクティブ購読の確認
    existing = await db.scalar(
        select(MonitoringSubscription).where(
            MonitoringSubscription.subject_type == body.subject_type,
            MonitoringSubscription.subject_id == body.subject_id,
            MonitoringSubscription.trigger_type == body.trigger_type,
            MonitoringSubscription.is_active.is_(True),
        )
    )
    if existing:
        return {"subscription": _sub_to_dict(existing), "created": False}

    sub = MonitoringSubscription(
        subject_type=body.subject_type,
        subject_id=body.subject_id,
        trigger_type=body.trigger_type,
        monitor_until=body.monitor_until,
        created_from_if=body.created_from_if,
    )
    db.add(sub)
    try:
        await db.commit()
        await db.refresh(sub)
    except IntegrityError:
        await db.rollback()
        # 競合発生時は既存を再取得
        existing = await db.scalar(
            select(MonitoringSubscription).where(
                MonitoringSubscription.subject_type == body.subject_type,
                MonitoringSubscription.subject_id == body.subject_id,
                MonitoringSubscription.trigger_type == body.trigger_type,
                MonitoringSubscription.is_active.is_(True),
            )
        )
        if existing:
            return {"subscription": _sub_to_dict(existing), "created": False}
        raise
    return {"subscription": _sub_to_dict(sub), "created": True}


@router.get("/subscriptions", response_model=dict)
async def list_subscriptions(
    subject_type: Optional[str] = None,
    subject_id: Optional[uuid.UUID] = None,
    is_active: Optional[bool] = True,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """購読一覧を返す。デフォルトはアクティブのみ。"""
    q = select(MonitoringSubscription).order_by(MonitoringSubscription.created_at.desc()).limit(limit)
    if subject_type is not None:
        q = q.where(MonitoringSubscription.subject_type == subject_type)
    if subject_id is not None:
        q = q.where(MonitoringSubscription.subject_id == subject_id)
    if is_active is not None:
        q = q.where(MonitoringSubscription.is_active.is_(is_active))
    result = await db.execute(q)
    subs = result.scalars().all()
    return {"subscriptions": [_sub_to_dict(s) for s in subs], "total": len(subs)}


@router.post("/run-now", response_model=dict)
async def trigger_monitoring_run() -> Any:
    """監視バッチを即時実行する（IF-11 疎通確認・テスト用）。

    通常は 24 時間ごとにバックグラウンドで実行されるが、
    このエンドポイントを叩くと即座に全アクティブ購読を処理する。
    """
    try:
        result = await run_monitoring_once()
        return {"ok": True, **result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/subscriptions/{subscription_id}", response_model=dict)
async def deactivate_subscription(
    subscription_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """購読を非アクティブ化する（物理削除ではなく論理削除）。"""
    sub = await db.get(MonitoringSubscription, subscription_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    if not sub.is_active:
        return {"ok": True, "subscription_id": str(subscription_id), "already_inactive": True}
    sub.is_active = False
    await db.commit()
    return {"ok": True, "subscription_id": str(subscription_id), "already_inactive": False}
