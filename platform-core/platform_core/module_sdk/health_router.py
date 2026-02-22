"""
標準ヘルスチェックルーター。

全モジュールの main.py に以下の一行を追加するだけで
platform-core からの死活監視に対応できる。

    from platform_core.module_sdk import health_router
    app.include_router(health_router)
"""

from fastapi import APIRouter

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}
