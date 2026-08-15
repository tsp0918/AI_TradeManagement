"""HMAC-SHA256 署名・検証ユーティリティ。

送信側（outbound）: CRM/ERP への Webhook 送信時にヘッダーへ署名を付与する。
受信側（inbound）:  CRM からのリクエスト受信時に X-Signature を検証する。

署名フォーマット: sha256=<hex>
署名対象:         "{timestamp}.{body_bytes}"
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import time

from fastapi import Depends, HTTPException, Request


def sign_payload(body: bytes, secret: str, timestamp: str) -> str:
    """outbound 送信用: ペイロードに HMAC-SHA256 署名を付与して返す。"""
    mac = _hmac.new(
        secret.encode(),
        f"{timestamp}.".encode() + body,
        hashlib.sha256,
    )
    return f"sha256={mac.hexdigest()}"


def verify_signature(
    body: bytes,
    signature: str,
    secret: str,
    timestamp: str,
    tolerance_sec: int = 300,
) -> bool:
    """inbound 受信用: X-Signature と X-Timestamp を検証する。

    - タイムスタンプが ±tolerance_sec (デフォルト 5分) 以内であること
    - HMAC が一致すること（タイミング攻撃耐性あり: compare_digest）
    """
    try:
        if abs(time.time() - int(timestamp)) > tolerance_sec:
            return False
    except (ValueError, TypeError):
        return False
    expected = sign_payload(body, secret, timestamp)
    return _hmac.compare_digest(expected, signature)


def require_crm_hmac(signing_secret: str):
    """FastAPI 依存関数ファクトリ。

    CRM 受信エンドポイントに `Depends(require_crm_hmac(secret))` で適用する。

    Usage:
        @router.post("/api/crm/provisional-review")
        async def create_provisional(
            _: None = Depends(require_crm_hmac(settings.crm_inbound_signing_secret)),
        ):
            ...
    """
    async def _verify(request: Request) -> None:
        body = await request.body()
        ts = request.headers.get("X-Timestamp", "")
        sig = request.headers.get("X-Signature", "")
        if not verify_signature(body, sig, signing_secret, ts):
            raise HTTPException(status_code=401, detail="Invalid HMAC signature")

    return Depends(_verify)
