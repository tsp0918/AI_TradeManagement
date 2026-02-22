"""AuditLog 自動記録ミドルウェア。

全リクエストを plat_audit_log テーブルに非同期記録する。

特徴:
- JWT クレームから user_id / tenant_id を取得（DBアクセスなし）
- レスポンス送信後にバックグラウンドで書き込み（レイテンシ影響なし）
- /health, /docs, /redoc, /openapi.json は除外

使い方（platform-core の main.py）:
    from platform_core.middleware.audit import AuditMiddleware
    app.add_middleware(AuditMiddleware, module_key="platform-core")

使い方（各モジュール）:
    from platform_core.middleware.audit import AuditMiddleware
    app.add_middleware(AuditMiddleware, module_key="ai_validation")
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Callable

from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from platform_core.config import settings
from platform_core.db.session import AsyncSessionLocal
from platform_core.models.audit import AuditLog

logger = logging.getLogger(__name__)

# ログ対象外パス（完全一致）
_SKIP_EXACT = frozenset({
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
})

# ログ対象外パス（プレフィックス）
_SKIP_PREFIXES = ("/docs/", "/redoc/")

# HTTP メソッド → action 文字列
_METHOD_TO_ACTION = {
    "GET": "read",
    "POST": "create",
    "PUT": "update",
    "PATCH": "update",
    "DELETE": "delete",
    "HEAD": "read",
    "OPTIONS": "preflight",
}

# パス末尾キーワード → action 上書き
_PATH_ACTION_OVERRIDES = {
    "login": "login",
    "logout": "logout",
    "refresh": "token_refresh",
    "register": "register",
    "callback": "sso_callback",
    "run": "run_ai",
    "export": "export",
}


def _extract_jwt_claims(request: Request) -> dict:
    """Authorization ヘッダから JWT クレームを返す（DB アクセスなし）。

    トークンが無い / 不正な場合は空 dict を返す。
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return {}
    token = auth[7:]
    try:
        return jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        return {}


def _parse_action_resource(
    method: str, path: str
) -> tuple[str, str | None, str | None]:
    """HTTP メソッドとパスから (action, resource_type, resource_id) を推定する。

    例:
        PUT  /admin/tenants/{uuid}        →  ("update",   "tenants",  "{uuid}")
        POST /auth/login                  →  ("login",    "auth",     None)
        POST /internal/modules/register   →  ("register", "modules",  None)
        GET  /admin/users                 →  ("read",     "users",    None)
    """
    action = _METHOD_TO_ACTION.get(method.upper(), method.lower())
    parts = [p for p in path.strip("/").split("/") if p]

    if not parts:
        return action, None, None

    resource_type: str | None = None
    resource_id: str | None = None

    # パスキーワードによる action 上書きを検索
    keyword_idx: int | None = None
    for i, part in enumerate(parts):
        if part in _PATH_ACTION_OVERRIDES:
            action = _PATH_ACTION_OVERRIDES[part]
            keyword_idx = i
            break  # 最初に見つかったキーワードを使用

    if keyword_idx is not None:
        # キーワードより前のセグメントからリソースを決定
        pre = parts[:keyword_idx]
        if pre:
            last_pre = pre[-1]
            if len(pre) >= 2 and _looks_like_id(last_pre):
                resource_id = last_pre
                resource_type = pre[-2]
            else:
                resource_type = last_pre
    else:
        # キーワードなし: 末尾セグメントが ID かどうかで分岐
        last = parts[-1]
        if len(parts) >= 2 and _looks_like_id(last):
            resource_id = last
            resource_type = parts[-2]
        else:
            resource_type = last

    return action, resource_type, resource_id


def _looks_like_id(value: str) -> bool:
    """UUID または純粋な数値ならリソース ID とみなす。

    一般的な英単語（"register", "login" など）を ID と誤認しないよう、
    UUID 形式と純数値のみを ID として扱う。
    """
    # UUID チェック（ハイフンあり / なし両対応）
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        pass
    # 純粋な数値 (DB 主キーなど)
    return value.isdigit()


def _get_client_ip(request: Request) -> str | None:
    """クライアント IP を取得する（プロキシ経由を考慮）。"""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


class AuditMiddleware(BaseHTTPMiddleware):
    """全リクエストを plat_audit_log に記録するミドルウェア。

    Args:
        module_key: 記録する module_key。省略時は "platform-core"。

    注意:
        add_middleware() でアタッチする場合、CORSMiddleware より後（内側）に
        追加すると SSO リダイレクトなども正しく記録される。
    """

    def __init__(self, app, module_key: str = "platform-core") -> None:
        super().__init__(app)
        self.module_key = module_key

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # スキップ判定
        if path in _SKIP_EXACT or path.startswith(_SKIP_PREFIXES):
            return await call_next(request)

        start = time.monotonic()
        response = await call_next(request)
        duration_ms = int((time.monotonic() - start) * 1000)

        # バックグラウンドで記録（失敗してもレスポンスには影響しない）
        asyncio.create_task(
            self._write_log(request, response.status_code, duration_ms)
        )

        return response

    async def _write_log(
        self,
        request: Request,
        status_code: int,
        duration_ms: int,
    ) -> None:
        claims = _extract_jwt_claims(request)
        raw_user_id = claims.get("sub")
        raw_tenant_id = claims.get("tenant_id")

        try:
            user_id = uuid.UUID(raw_user_id) if raw_user_id else None
        except ValueError:
            user_id = None

        try:
            tenant_id = uuid.UUID(raw_tenant_id) if raw_tenant_id else None
        except ValueError:
            tenant_id = None

        action, resource_type, resource_id = _parse_action_resource(
            request.method, request.url.path
        )

        detail: dict = {
            "method": request.method,
            "path": request.url.path,
            "status_code": status_code,
            "duration_ms": duration_ms,
        }
        if str(request.query_params):
            detail["query"] = str(request.query_params)

        try:
            async with AsyncSessionLocal() as session:
                log = AuditLog(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    module_key=self.module_key,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    detail=detail,
                    ip_address=_get_client_ip(request),
                )
                session.add(log)
                await session.commit()
        except Exception as exc:
            # 監査ログ書き込み失敗はデバッグログのみ（本体処理は継続）
            logger.debug("AuditMiddleware: failed to write audit log: %s", exc)
