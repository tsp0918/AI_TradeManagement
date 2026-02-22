"""
内部サービス間認証。

モジュール → platform-core の内部 API 呼び出しには
X-Internal-Service-Key ヘッダーを使用する。
ユーザーの JWT とは別管理で、.env の INTERNAL_SERVICE_KEY に設定する。
"""

import os
from fastapi import HTTPException, Request, status

INTERNAL_SERVICE_KEY_HEADER = "X-Internal-Service-Key"


def get_internal_auth_header() -> dict[str, str]:
    """HTTP リクエスト用のヘッダーを返す。"""
    key = os.environ.get("INTERNAL_SERVICE_KEY", "")
    if not key:
        return {}
    return {INTERNAL_SERVICE_KEY_HEADER: key}


async def verify_internal_key(request: Request) -> None:
    """
    FastAPI 依存注入: 内部サービスキーを検証する。

    使い方（platform-core の内部エンドポイント）:
        @router.post("/internal/modules/register")
        async def register(_: None = Depends(verify_internal_key)):
            ...
    """
    expected = os.environ.get("INTERNAL_SERVICE_KEY", "")
    if not expected:
        # キー未設定時は開発環境とみなしスキップ
        return

    provided = request.headers.get(INTERNAL_SERVICE_KEY_HEADER, "")
    if not provided or provided != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing internal service key",
        )
