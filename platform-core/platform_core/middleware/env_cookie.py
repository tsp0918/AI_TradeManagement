"""ミドルウェア: ?env= クエリパラメータを ai_tm_env クッキーに変換する。

ポータル (port 8000) が iframe URL に ?env=e2e_test を付与する。
各モジュールはこのミドルウェアで受け取り、自分自身のオリジンに
ai_tm_env クッキーをセットする。以降そのモジュール内の全リクエスト
(JS fetch・リンク遷移) でクッキーが送られ、session.py が正しい DB に
ルーティングできる。
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_ALLOWED_ENVS = frozenset({"production", "e2e_test"})


class EnvCookieMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        env = request.query_params.get("env", "")
        if env not in _ALLOWED_ENVS:
            return response
        if env == "production":
            response.delete_cookie("ai_tm_env", path="/")
        else:
            response.set_cookie(
                "ai_tm_env", env,
                max_age=86400, path="/", samesite="lax", httponly=False,
            )
        return response
