"""サプライヤーポータルプロキシ — ai_classification モジュールへ委譲。

Phase 6A-2: サプライヤーポータルは ai_classification モジュール (port 8002) に移管。
platform-core の /api/supplier-portal/* および /supplier-portal/* は全リクエストを転送する。
"""

import os

import httpx
from fastapi import APIRouter, Request, Response

router = APIRouter(tags=["supplier-portal-proxy"])

_AI_CLASSIFICATION_URL = os.environ.get("MODULE_AI_CLASSIFICATION_URL", "http://localhost:8002")


async def _proxy(request: Request, target_path: str) -> Response:
    url = f"{_AI_CLASSIFICATION_URL}/{target_path.lstrip('/')}"
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


@router.api_route("/api/supplier-portal/tokens", methods=["GET", "POST"])
async def portal_tokens_root(request: Request):
    return await _proxy(request, "/api/supplier-portal/tokens")


@router.api_route("/api/supplier-portal/tokens/{path:path}", methods=["GET", "POST"])
async def portal_tokens_path(request: Request, path: str):
    return await _proxy(request, f"/api/supplier-portal/tokens/{path}")


@router.api_route("/supplier-portal/{path:path}", methods=["GET", "POST"])
async def supplier_portal_path(request: Request, path: str):
    return await _proxy(request, f"/supplier-portal/{path}")
