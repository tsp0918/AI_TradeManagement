"""サプライチェーン管理プロキシ — ai_classification モジュールへ委譲。

Phase 6A-2: サプライチェーン管理は ai_classification モジュール (port 8002) に移管。
platform-core の /api/supply-chain/* は全リクエストを ai_classification に転送する。
"""

import os
import io

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

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


@router.api_route("/api/supply-chain/bom-attestation-summary/{product_code}", methods=["GET"])
async def supply_chain_bom_attestation_summary(request: Request, product_code: str):
    return await _proxy(request, f"by-product-code/{product_code}/bom-attestation-summary")


@router.api_route("/api/supply-chain/impact/{path:path}", methods=["GET"])
async def supply_chain_impact(request: Request, path: str):
    return await _proxy(request, f"impact/{path}")


@router.get("/ui/product-by-code/{code}")
async def redirect_product_by_code(code: str):
    """品目コードで品目編集ページにリダイレクト（デモ・UC用）。"""
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        r = await client.get(f"{_AI_CLASSIFICATION_URL}/api/products/by-code/{code}")
        if r.status_code != 200:
            return Response(
                content=f"品目コード '{code}' が品目管理に見つかりません。先に品目を登録してください。",
                status_code=404,
                media_type="text/plain; charset=utf-8",
            )
        product_id = r.json()["id"]
    return RedirectResponse(url=f"/proxy/ai_classification/products/{product_id}/edit", status_code=302)


@router.post("/api/products/by-code/{code}/bom-add-item")
async def bom_add_item_by_code(code: str, request: Request):
    """製品コード指定でBOM構成品を追加するプロキシ（DEMO api_call 用）。"""
    body = await request.json()
    base = _AI_CLASSIFICATION_URL

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{base}/api/products/by-code/{code}")
        if r.status_code != 200:
            return JSONResponse({"error": f"Product not found: {code}"}, status_code=404)
        product_id = r.json()["id"]

        unit_val = body.get("unit_value")
        r2 = await client.post(
            f"{base}/products/{product_id}/bom-add-item",
            data={
                "component_code": body.get("component_code", ""),
                "component_name": body.get("component_name", ""),
                "coo": body.get("coo", ""),
                "ratio": str(body.get("ratio", 1.0)),
                "unit_value": str(unit_val) if unit_val is not None else "",
            },
            follow_redirects=False,
        )

    return JSONResponse({
        "status": "ok",
        "product_code": code,
        "product_id": product_id,
        "component_code": body.get("component_code"),
        "http_status": r2.status_code,
    })


@router.post("/api/demo/seed-demo3")
async def seed_demo3():
    """DEMO3用デモデータ: ai_classification に両製品＋BOM CSVを登録し SC同期まで実行する。
    冪等 — 既存データがある場合はスキップ。
    """
    base = _AI_CLASSIFICATION_URL

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        # ① US-LASER-DM650 を ai_classification に upsert（構成品）
        r1 = await client.post(f"{base}/products/erp-sync", json={
            "code": "US-LASER-DM650",
            "name": "Laser Diode Module 650nm (US Origin)",
            "eccn": "6A005.a.2",
            "country_of_origin": "US",
            "item_type": "PURCHASED_PART",
            "std_price": 850.0,
            "description": "米国原産レーザーダイオードモジュール650nm。EAR管理品 ECCN 6A005.a.2"
        })
        laser_product_id = r1.json().get("id") if r1.status_code == 200 else None

        # ② LS-500 を ai_classification に upsert（完成品）
        r2 = await client.post(f"{base}/products/erp-sync", json={
            "code": "LS-500",
            "name": "レーザー変位センサー Model LS-500",
            "eccn": "3A002",
            "country_of_origin": "JP",
            "item_type": "FINISHED_GOODS",
            "std_price": 2400.0,
            "description": "半導体ウェハー表面検査用レーザー変位センサー（650nm、分解能0.1μm）"
        })
        ls500_product_id = r2.json().get("id") if r2.status_code == 200 else None

        bom_synced = False
        if ls500_product_id and laser_product_id:
            # ③ LS-500 の BOM CSV をアップロード（US-LASER-DM650 を構成品として登録）
            # BOM CSV upload は自動的に SC同期（bom_sync）を実行する
            bom_csv = (
                "component_code,component_name,ratio,kind,coo,unit_value\n"
                "US-LASER-DM650,Laser Diode Module 650nm (US Origin),0.354,material,US,850.0\n"
            )
            upload_resp = await client.post(
                f"{base}/products/{ls500_product_id}/bom",
                files={"file": ("bom_ls500.csv", bom_csv.encode("utf-8"), "text/csv")},
            )
            # 303 redirect = success
            bom_synced = upload_resp.status_code in (200, 303)

    return JSONResponse({
        "status": "ok",
        "ls500_product_id": ls500_product_id,
        "laser_product_id": laser_product_id,
        "bom_synced": bom_synced,
    })
