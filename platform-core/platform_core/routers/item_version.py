"""品目バージョン管理プロキシ — ai_classification モジュールへ委譲。

Phase 6A-2: 品目バージョン管理は ai_classification モジュール (port 8002) に移管。
platform-core の /api/item-versions/* は全リクエストを ai_classification に転送する。

注意: /api/item-versions/sync-preview と /api/item-versions/sync-to-erp は
      trade_gate モジュール (port 8013) に登録されているため、そちらへ転送する。
      FastAPI はルート登録順に一致するため、ここで先に明示的に定義する。
"""

import os

import httpx
from fastapi import APIRouter, Request, Response

router = APIRouter(tags=["item-version-proxy"])

_AI_CLASSIFICATION_URL = os.environ.get("MODULE_AI_CLASSIFICATION_URL", "http://localhost:8002")
_TRADE_GATE_URL = os.environ.get("MODULE_TRADE_GATE_URL", "http://localhost:8013")


async def _proxy_to(target_url: str, request: Request) -> Response:
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
    body = await request.body()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(
            request.method, target_url, content=body, headers=headers,
            params=dict(request.query_params),
        )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
        media_type=resp.headers.get("content-type"),
    )


# sync-preview / sync-to-erp は trade_gate (8013) へ — ワイルドカードより先に登録
@router.api_route("/api/item-versions/sync-preview", methods=["GET"])
async def sync_preview_proxy(request: Request) -> Response:
    return await _proxy_to(f"{_TRADE_GATE_URL}/api/item-versions/sync-preview", request)


@router.api_route("/api/item-versions/sync-to-erp", methods=["POST"])
async def sync_to_erp_proxy(request: Request) -> Response:
    return await _proxy_to(f"{_TRADE_GATE_URL}/api/item-versions/sync-to-erp", request)


# その他 /api/item-versions/* は ai_classification (8002) へ
@router.api_route("/api/item-versions", methods=["GET"])
async def item_versions_root(request: Request) -> Response:
    return await _proxy_to(f"{_AI_CLASSIFICATION_URL}/api/item-versions", request)


@router.api_route("/api/item-versions/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def item_versions_path(request: Request, path: str) -> Response:
    return await _proxy_to(f"{_AI_CLASSIFICATION_URL}/api/item-versions/{path}", request)
