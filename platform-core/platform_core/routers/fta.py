"""EPA/FTA 特恵税率プロキシ — fta_origin モジュールへ委譲。

Phase 6B-2: FTA/EPA 管理は fta_origin モジュール (port 8014) に移管。
platform-core の /api/fta/* および /ui/fta-check は全リクエストを fta_origin に転送する。
"""

import os

import httpx
from fastapi import APIRouter, Request, Response

router = APIRouter(tags=["fta-proxy"])

_FTA_ORIGIN_URL = os.environ.get("MODULE_FTA_ORIGIN_URL", "http://localhost:8014")


async def _proxy(request: Request, path: str) -> Response:
    url = f"{_FTA_ORIGIN_URL}/{path}".rstrip("/")
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


@router.api_route("/api/fta/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_fta_api(request: Request, path: str) -> Response:
    return await _proxy(request, f"api/fta/{path}")


@router.api_route("/ui/fta-check", methods=["GET"])
async def proxy_fta_ui(request: Request) -> Response:
    return await _proxy(request, "ui/fta-check")
