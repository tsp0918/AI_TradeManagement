"""継続モニタリング管理 API（Phase 5）。

エンドポイント:
  POST   /api/monitoring/subscriptions          購読を作成（または既存を返す）
  GET    /api/monitoring/subscriptions          購読一覧
  DELETE /api/monitoring/subscriptions/{id}     購読を非アクティブ化
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.db.session import get_db
from platform_core.models.monitoring import MonitoringSubscription

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])


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
