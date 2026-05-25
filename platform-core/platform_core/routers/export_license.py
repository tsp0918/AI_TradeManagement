"""輸出許可申請プロキシ — export_license モジュールへ委譲。

Phase 6B-1: 輸出許可申請管理は export_license モジュール (port 8012) に移管。
platform-core の /api/export-licenses/* は全リクエストを export_license に転送する。
"""

import os

import httpx
from fastapi import APIRouter, Request, Response

router = APIRouter(tags=["export-license-proxy"])

_EXPORT_LICENSE_URL = os.environ.get("MODULE_EXPORT_LICENSE_URL", "http://localhost:8012")


async def _proxy(request: Request, path: str) -> Response:
    url = f"{_EXPORT_LICENSE_URL}/api/export-licenses/{path}".rstrip("/")
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


@router.api_route("/api/export-licenses/stats", methods=["GET"])
async def proxy_stats(request: Request) -> Response:
    return await _proxy(request, "stats")


@router.api_route("/api/export-licenses/draft-from-transaction", methods=["POST"])
async def proxy_draft_from_tx(request: Request) -> Response:
    return await _proxy(request, "draft-from-transaction")


@router.api_route("/api/export-licenses/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_licenses(request: Request, path: str) -> Response:
    return await _proxy(request, path)
