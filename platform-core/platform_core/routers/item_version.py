"""品目バージョン管理プロキシ — ai_classification モジュールへ委譲。

Phase 6A-2: 品目バージョン管理は ai_classification モジュール (port 8002) に移管。
platform-core の /api/item-versions/* は全リクエストを ai_classification に転送する。
"""

import os

import httpx
from fastapi import APIRouter, Request, Response

router = APIRouter(tags=["item-version-proxy"])

_AI_CLASSIFICATION_URL = os.environ.get("MODULE_AI_CLASSIFICATION_URL", "http://localhost:8002")


async def _proxy(request: Request, path: str) -> Response:
    url = f"{_AI_CLASSIFICATION_URL}/api/item-versions/{path}".rstrip("/")
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
    body = await request.body()

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(
            request.method, url, content=body, headers=headers,
            params=dict(request.query_params),
        )

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
        media_type=resp.headers.get("content-type"),
    )


@router.api_route("/api/item-versions", methods=["GET"])
async def item_versions_root(request: Request):
    return await _proxy(request, "")


@router.api_route("/api/item-versions/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def item_versions_path(request: Request, path: str):
    return await _proxy(request, path)
