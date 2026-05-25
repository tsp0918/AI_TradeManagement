"""与信管理プロキシ — screening モジュールへ委譲。

Phase 6A-1: 与信管理は screening モジュール (port 8005) に移管。
platform-core の /api/counterparties/* は全リクエストを screening に転送する。

後方互換性のためこのルーターは残存するが、実装は screening 側にある。
"""

import os

import httpx
from fastapi import APIRouter, Request, Response

router = APIRouter(tags=["counterparty-proxy"])

_SCREENING_URL = os.environ.get("MODULE_SCREENING_URL", "http://localhost:8005")


async def _proxy(request: Request, path: str) -> Response:
    url = f"{_SCREENING_URL}/api/counterparties/{path}".rstrip("/")
    method = request.method
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
    body = await request.body()

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(method, url, content=body, headers=headers,
                                    params=dict(request.query_params))

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
        media_type=resp.headers.get("content-type"),
    )


@router.api_route("/api/counterparties", methods=["GET", "POST"])
async def counterparties_root(request: Request):
    return await _proxy(request, "")


@router.api_route("/api/counterparties/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def counterparties_path(request: Request, path: str):
    return await _proxy(request, path)
