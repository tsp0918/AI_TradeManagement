"""サプライチェーン管理プロキシ — ai_classification モジュールへ委譲。

Phase 6A-2: サプライチェーン管理は ai_classification モジュール (port 8002) に移管。
platform-core の /api/supply-chain/* は全リクエストを ai_classification に転送する。
"""

import os

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

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


@router.api_route("/api/supply-chain/by-product-code/{path:path}", methods=["GET"])
async def supply_chain_by_product_code(request: Request, path: str):
    return await _proxy(request, f"by-product-code/{path}")


@router.api_route("/api/supply-chain/impact/{path:path}", methods=["GET"])
async def supply_chain_impact(request: Request, path: str):
    return await _proxy(request, f"impact/{path}")


@router.post("/api/demo/seed-demo3")
async def seed_demo3():
    """DEMO3用デモデータ: LS-500 親ノード + US-LASER-DM650 子ノード + BOMエッジを作成する。
    既に存在する場合はスキップして既存IDを返す。
    """
    base = _AI_CLASSIFICATION_URL

    async with httpx.AsyncClient(timeout=30.0) as client:
        # LS-500 存在確認 (by-product-code は {"node": {...} or null, "children": [...]} を返す)
        r = await client.get(f"{base}/api/supply-chain/by-product-code/LS-500")
        bpc = r.json() if r.status_code == 200 else {}
        ls500_node = bpc.get("node") if isinstance(bpc, dict) else None

        if ls500_node:
            parent_id = ls500_node["id"]
        else:
            resp = await client.post(f"{base}/api/supply-chain/nodes", json={
                "name": "レーザー変位センサー Model LS-500",
                "part_number": "LS-500",
                "product_code": "LS-500",
                "node_type": "product",
                "country_of_origin": "JP",
                "is_us_origin": False,
                "eccn": "3A002",
                "unit_value_usd": 2400.0,
                "description": "半導体ウェハー表面検査用レーザー変位センサー"
            })
            parent_id = resp.json().get("id")

        # US-LASER-DM650 存在確認
        r2 = await client.get(f"{base}/api/supply-chain/by-product-code/US-LASER-DM650")
        bpc2 = r2.json() if r2.status_code == 200 else {}
        child_node = bpc2.get("node") if isinstance(bpc2, dict) else None

        if child_node:
            child_id = child_node["id"]
        else:
            resp2 = await client.post(f"{base}/api/supply-chain/nodes", json={
                "name": "Laser Diode Module 650nm (US Origin)",
                "part_number": "US-LASER-DM650",
                "product_code": "US-LASER-DM650",
                "node_type": "component",
                "country_of_origin": "US",
                "is_us_origin": True,
                "eccn": "6A005.a.2",
                "unit_value_usd": 850.0,
                "us_controlled_value_usd": 850.0,
                "description": "米国原産レーザーダイオードモジュール650nm ECCN:6A005.a.2"
            })
            child_id = resp2.json().get("id")

        # BOMエッジ作成（409 重複は無視）
        if parent_id and child_id:
            await client.post(f"{base}/api/supply-chain/edges", json={
                "parent_node_id": str(parent_id),
                "child_node_id": str(child_id),
                "quantity": 1.0,
                "unit": "each"
            })

    return JSONResponse({"status": "ok", "parent_id": parent_id, "child_id": child_id})
