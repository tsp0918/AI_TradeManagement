"""ERP プル型レビュー プロキシ — erp_integration モジュールへ委譲。

AI Trade Management 側から発行する ERP マスターデータ取得＋審査リクエストを
erp_integration (port 5001) の /pull/* へ転送する。
"""

import os

import httpx
from fastapi import APIRouter, Request, Response

router = APIRouter(tags=["erp-pull-proxy"])

_ERP_ADAPTER_URL = os.environ.get("ERP_ADAPTER_URL", "http://localhost:5001")


async def _proxy(request: Request, path: str) -> Response:
    url = f"{_ERP_ADAPTER_URL}/{path}".rstrip("/")
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
    body = await request.body()

    async with httpx.AsyncClient(timeout=60.0) as client:
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


@router.post("/api/erp/pull/item-review")
async def proxy_item_review(request: Request) -> Response:
    return await _proxy(request, "pull/item-review")


@router.post("/api/erp/pull/items-batch-review")
async def proxy_items_batch_review(request: Request) -> Response:
    return await _proxy(request, "pull/items-batch-review")


@router.post("/api/erp/pull/counterparty-screen")
async def proxy_counterparty_screen(request: Request) -> Response:
    return await _proxy(request, "pull/counterparty-screen")


@router.post("/api/erp/pull/counterparties-batch-screen")
async def proxy_counterparties_batch_screen(request: Request) -> Response:
    return await _proxy(request, "pull/counterparties-batch-screen")
