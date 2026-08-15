"""Webhook 配信サービス。

CRM/ERP への外部 Webhook 送信・リトライ・DLQ 管理を一元化する。

送信フロー:
  1. enqueue() を呼び出す
     → webhook_delivery レコードを作成 (status='pending')
     → 即時配信を fire-and-forget で試行
  2. 配信成功 → status='delivered'
  3. 配信失敗 → attempt_count++, next_retry_at を指数バックオフで設定
  4. 6回失敗   → status='dlq'（デッドレターキュー）
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.auth.hmac import sign_payload
from platform_core.models.webhook import WebhookDelivery, WebhookEndpoint

logger = logging.getLogger(__name__)

# バックオフスケジュール（秒）: 10s → 60s → 5m → 30m → 2h
_RETRY_DELAYS = [10, 60, 300, 1800, 7200]
_MAX_ATTEMPTS = 6


def _compute_next_retry(attempt_count: int) -> datetime:
    """次回リトライ時刻を計算する（指数バックオフ + ±10% ジッター）。"""
    idx = min(attempt_count, len(_RETRY_DELAYS) - 1)
    base = _RETRY_DELAYS[idx]
    jitter = base * random.uniform(-0.1, 0.1)
    delay = max(1, int(base + jitter))
    return datetime.now(tz=timezone.utc) + timedelta(seconds=delay)


def _build_headers(body: bytes, signing_secret: str) -> dict[str, str]:
    ts = str(int(time.time()))
    return {
        "Content-Type": "application/json",
        "X-Timestamp": ts,
        "X-Signature": sign_payload(body, signing_secret, ts),
    }


def _deliver_sync(
    delivery_id: uuid.UUID,
    url: str,
    signing_secret: str,
    payload: dict,
) -> None:
    """同期 HTTP 配信（threading.Thread の中で実行される）。

    成否に応じて webhook_delivery を直接 SQLAlchemy 同期セッションで更新する。
    """
    from platform_core.db.session import SyncSessionLocal

    body = json.dumps(payload, ensure_ascii=False).encode()
    headers = _build_headers(body, signing_secret)

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(url, content=body, headers=headers)
            resp.raise_for_status()
        # 配信成功
        with SyncSessionLocal() as db:
            delivery = db.get(WebhookDelivery, delivery_id)
            if delivery:
                delivery.status = "delivered"
                delivery.delivered_at = datetime.now(tz=timezone.utc)
                db.commit()
        logger.info("Webhook delivered: delivery_id=%s url=%s", delivery_id, url)
    except Exception as exc:
        # 配信失敗 → リトライキューへ
        with SyncSessionLocal() as db:
            delivery = db.get(WebhookDelivery, delivery_id)
            if delivery:
                delivery.attempt_count += 1
                delivery.last_error = str(exc)[:500]
                if delivery.attempt_count >= _MAX_ATTEMPTS:
                    delivery.status = "dlq"
                    logger.warning(
                        "Webhook DLQ: delivery_id=%s url=%s attempts=%d error=%s",
                        delivery_id, url, delivery.attempt_count, exc,
                    )
                else:
                    delivery.status = "failed"
                    delivery.next_retry_at = _compute_next_retry(delivery.attempt_count)
                    logger.warning(
                        "Webhook failed (attempt %d/%d): delivery_id=%s error=%s next_retry=%s",
                        delivery.attempt_count, _MAX_ATTEMPTS, delivery_id, exc,
                        delivery.next_retry_at,
                    )
                db.commit()


class WebhookDispatcher:
    """Webhook 配信の入口。各モジュールはこのクラスを通じて外部通知を送信する。"""

    @staticmethod
    async def enqueue(
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID | None,
        event_type: str,
        payload: dict,
        target_system: str = "crm",
    ) -> uuid.UUID | None:
        """webhook_delivery レコードを作成し、即時配信を非同期（スレッド）で試みる。

        Args:
            db:            AsyncSession（呼び出し元のセッションを使用）
            tenant_id:     対象テナントの UUID
            event_type:    イベント種別（例: 'review.status_changed'）
            payload:       送信するペイロード dict
            target_system: 送信先システム（'crm' | 'erp'）

        Returns:
            作成した WebhookDelivery の UUID、または エンドポイント未設定なら None
        """
        if not tenant_id:
            return None

        endpoint = await db.scalar(
            select(WebhookEndpoint).where(
                WebhookEndpoint.tenant_id == tenant_id,
                WebhookEndpoint.target_system == target_system,
                WebhookEndpoint.is_active == True,  # noqa: E712
            ).limit(1)
        )
        if not endpoint:
            logger.debug(
                "No active webhook endpoint for tenant=%s system=%s", tenant_id, target_system
            )
            return None

        delivery = WebhookDelivery(
            endpoint_id=endpoint.id,
            event_type=event_type,
            payload=payload,
            status="pending",
            next_retry_at=None,
        )
        db.add(delivery)
        await db.flush()
        delivery_id = delivery.id

        # 即時配信（fire-and-forget）— commit は呼び出し元が責任を持つ
        url = endpoint.url
        secret = endpoint.signing_secret
        threading.Thread(
            target=_deliver_sync,
            args=(delivery_id, url, secret, payload),
            daemon=True,
        ).start()

        return delivery_id

    @staticmethod
    async def retry_pending(db: AsyncSession) -> int:
        """リトライ対象の配信を処理する（webhook_retry_worker から呼ばれる）。

        Returns:
            処理したレコード数
        """
        now = datetime.now(tz=timezone.utc)
        result = await db.execute(
            select(WebhookDelivery, WebhookEndpoint)
            .join(WebhookEndpoint, WebhookDelivery.endpoint_id == WebhookEndpoint.id)
            .where(
                WebhookDelivery.status.in_(["pending", "failed"]),
                WebhookDelivery.next_retry_at <= now,
            )
            .limit(50)
        )
        rows = result.all()
        for delivery, endpoint in rows:
            threading.Thread(
                target=_deliver_sync,
                args=(delivery.id, endpoint.url, endpoint.signing_secret, delivery.payload),
                daemon=True,
            ).start()
        return len(rows)
