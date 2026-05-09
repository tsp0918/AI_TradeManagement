"""サプライチェーン管理プロキシ — ai_classification モジュールへ委譲。

Phase 6A-2: サプライチェーン管理は ai_classification モジュール (port 8002) に移管。
platform-core の /api/supply-chain/* は全リクエストを ai_classification に転送する。
"""

import os

import httpx
from fastapi import APIRouter, Request, Response

router = APIRouter(tags=["supply-chain-proxy"])

_AI_CLASSIFICATION_URL = os.environ.get("MODULE_AI_CLASSIFICATION_URL", "http://localhost:8002")


async def _proxy(request: Request, path: str) -> Response:
    url = f"{_AI_CLASSIFICATION_URL}/api/supply-chain/{path}".rstrip("/")
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


@router.api_route("/api/supply-chain/stats", methods=["GET"])
async def supply_chain_stats(request: Request):
    return await _proxy(request, "stats")


@router.api_route("/api/supply-chain/nodes", methods=["GET", "POST"])
async def supply_chain_nodes_root(request: Request):
    return await _proxy(request, "nodes")


@router.api_route("/api/supply-chain/edges", methods=["POST"])
async def supply_chain_edges_root(request: Request):
    return await _proxy(request, "edges")


@router.api_route("/api/supply-chain/edges/{path:path}", methods=["DELETE"])
async def supply_chain_edges_path(request: Request, path: str):
    return await _proxy(request, f"edges/{path}")


@router.api_route("/api/supply-chain/nodes/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def supply_chain_nodes_path(request: Request, path: str):
    return await _proxy(request, f"nodes/{path}")
