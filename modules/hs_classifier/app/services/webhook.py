"""webhook 送信ロジック（指数バックオフリトライ付き）。"""
from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def send_webhook(callback_url: str, payload: dict) -> bool:
    """callback_url に payload を POST する。失敗時は最大 max_retries 回リトライ。"""
    delay = settings.webhook_retry_delay

    for attempt in range(1, settings.webhook_max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=settings.webhook_timeout) as client:
                r = await client.post(callback_url, json=payload)
                if r.status_code < 500:
                    logger.info(
                        "Webhook 送信成功: %s → %d", callback_url, r.status_code
                    )
                    return True
                logger.warning(
                    "Webhook 送信失敗 (attempt %d/%d): status=%d",
                    attempt, settings.webhook_max_retries, r.status_code,
                )
        except Exception as exc:
            logger.warning(
                "Webhook 送信エラー (attempt %d/%d): %s",
                attempt, settings.webhook_max_retries, exc,
            )

        if attempt < settings.webhook_max_retries:
            await asyncio.sleep(delay)
            delay *= 2   # exponential backoff

    logger.error("Webhook 送信が %d 回失敗しました: %s", settings.webhook_max_retries, callback_url)
    return False
