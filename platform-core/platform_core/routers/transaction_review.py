"""取引審査プロキシ — trade_gate モジュールへ委譲。

Phase 6C-1: 取引審査管理は trade_gate モジュール (port 8013) に移管。
platform-core の /api/transaction-reviews/* および /api/item-versions/sync-* は
全リクエストを trade_gate に転送する。
"""

import os

import httpx
from fastapi import APIRouter, Request, Response

router = APIRouter(tags=["transaction-review-proxy"])

_TRADE_GATE_URL = os.environ.get("MODULE_TRADE_GATE_URL", "http://localhost:8013")


async def _proxy(request: Request, path: str) -> Response:
    url = f"{_TRADE_GATE_URL}/{path}".rstrip("/")
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


@router.api_route("/api/transaction-reviews/stats", methods=["GET"])
async def proxy_stats(request: Request) -> Response:
    return await _proxy(request, "api/transaction-reviews/stats")


@router.api_route("/api/transaction-reviews/check-and-link", methods=["POST"])
async def proxy_check_and_link(request: Request) -> Response:
    return await _proxy(request, "api/transaction-reviews/check-and-link")


@router.api_route("/api/transaction-reviews/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_reviews(request: Request, path: str) -> Response:
    return await _proxy(request, f"api/transaction-reviews/{path}")


@router.api_route("/api/item-versions/sync-preview", methods=["GET"])
async def proxy_sync_preview(request: Request) -> Response:
    return await _proxy(request, "api/item-versions/sync-preview")


@router.api_route("/api/item-versions/sync-to-erp", methods=["POST"])
async def proxy_sync_to_erp(request: Request) -> Response:
    return await _proxy(request, "api/item-versions/sync-to-erp")
