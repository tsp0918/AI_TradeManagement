"""Webhook 管理 API。

エンドポイント設定・配信状況確認・DLQ 手動再送を提供する。
Bearer トークン（platform-core JWT）認証必須。

Routes:
  GET  /api/webhooks/endpoints              — エンドポイント一覧
  POST /api/webhooks/endpoints              — エンドポイント登録
  DELETE /api/webhooks/endpoints/{id}       — エンドポイント削除（論理削除）
  GET  /api/webhooks/deliveries             — 配信履歴（フィルタ対応）
  POST /api/webhooks/deliveries/{id}/resend — DLQ 手動再送
  POST /internal/webhooks/dispatch          — モジュール間内部 dispatch 受付
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.auth.dependencies import get_current_user
from platform_core.db.session import get_db
from platform_core.models.webhook import WebhookDelivery, WebhookEndpoint
from platform_core.services.webhook import WebhookDispatcher

router = APIRouter(tags=["webhooks"])


# ──────────────────────────────────────────────────────────────────────────────
# スキーマ
# ──────────────────────────────────────────────────────────────────────────────

class EndpointCreate(BaseModel):
    tenant_id: uuid.UUID | None = None
    target_system: str  # 'crm' | 'erp'
    url: str
    signing_secret: str


class DispatchRequest(BaseModel):
    tenant_id: uuid.UUID | None = None
    event_type: str
    payload: dict[str, Any]
    target_system: str = "crm"


# ──────────────────────────────────────────────────────────────────────────────
# エンドポイント管理
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/webhooks/endpoints")
async def list_endpoints(
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(get_current_user),
):
    result = await db.execute(
        select(WebhookEndpoint).order_by(WebhookEndpoint.created_at.desc())
    )
    endpoints = result.scalars().all()
    return [
        {
            "id": str(ep.id),
            "tenant_id": str(ep.tenant_id) if ep.tenant_id else None,
            "target_system": ep.target_system,
            "url": ep.url,
            "is_active": ep.is_active,
            "created_at": ep.created_at.isoformat() if ep.created_at else None,
        }
        for ep in endpoints
    ]


@router.post("/api/webhooks/endpoints", status_code=201)
async def create_endpoint(
    body: EndpointCreate,
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(get_current_user),
):
    ep = WebhookEndpoint(
        tenant_id=body.tenant_id,
        target_system=body.target_system,
        url=body.url,
        signing_secret=body.signing_secret,
    )
    db.add(ep)
    await db.commit()
    await db.refresh(ep)
    return {"id": str(ep.id), "target_system": ep.target_system, "url": ep.url}


@router.delete("/api/webhooks/endpoints/{endpoint_id}", status_code=204)
async def deactivate_endpoint(
    endpoint_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(get_current_user),
):
    ep = await db.get(WebhookEndpoint, endpoint_id)
    if not ep:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    ep.is_active = False
    await db.commit()


# ──────────────────────────────────────────────────────────────────────────────
# 配信履歴・DLQ 管理
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/webhooks/deliveries")
async def list_deliveries(
    status: str | None = Query(None, description="pending/delivered/failed/dlq"),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(get_current_user),
):
    q = select(WebhookDelivery).order_by(WebhookDelivery.created_at.desc()).limit(limit)
    if status:
        q = q.where(WebhookDelivery.status == status)
    result = await db.execute(q)
    deliveries = result.scalars().all()
    return [
        {
            "id": str(d.id),
            "endpoint_id": str(d.endpoint_id),
            "event_type": d.event_type,
            "status": d.status,
            "attempt_count": d.attempt_count,
            "last_error": d.last_error,
            "next_retry_at": d.next_retry_at.isoformat() if d.next_retry_at else None,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None,
        }
        for d in deliveries
    ]


@router.post("/api/webhooks/deliveries/{delivery_id}/resend", status_code=202)
async def resend_delivery(
    delivery_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(get_current_user),
):
    """DLQ に入った配信を手動で再送する。"""
    delivery = await db.get(WebhookDelivery, delivery_id)
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found")

    endpoint = await db.get(WebhookEndpoint, delivery.endpoint_id)
    if not endpoint or not endpoint.is_active:
        raise HTTPException(status_code=409, detail="Endpoint inactive or not found")

    # リトライ可能な状態にリセット
    delivery.status = "pending"
    delivery.attempt_count = 0
    delivery.next_retry_at = None
    delivery.last_error = None
    await db.commit()

    # 即時再送
    from platform_core.services.webhook import _deliver_sync
    import threading
    threading.Thread(
        target=_deliver_sync,
        args=(delivery.id, endpoint.url, endpoint.signing_secret, delivery.payload),
        daemon=True,
    ).start()

    return {"queued": True, "delivery_id": str(delivery_id)}


# ──────────────────────────────────────────────────────────────────────────────
# 内部 dispatch エンドポイント（モジュール間通信用、認証は内部トークン）
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/internal/webhooks/dispatch", status_code=202)
async def internal_dispatch(
    body: DispatchRequest,
    db: AsyncSession = Depends(get_db),
):
    """ai_validation など他モジュールから Webhook 配信をキューに入れる。

    認証: AITM_INTERNAL_TOKEN ヘッダー（将来実装）。現在は内部ネットワーク前提。
    """
    delivery_id = await WebhookDispatcher.enqueue(
        db,
        tenant_id=body.tenant_id,
        event_type=body.event_type,
        payload=body.payload,
        target_system=body.target_system,
    )
    if delivery_id is None:
        return {"queued": False, "reason": "no_active_endpoint"}
    return {"queued": True, "delivery_id": str(delivery_id)}
