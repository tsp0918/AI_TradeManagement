"""
module_sdk.client
=================
platform-core 内部 API を呼び出す HTTP クライアント。

モジュール間通信にも使用する。
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from platform_core.module_sdk.internal_auth import INTERNAL_SERVICE_KEY_HEADER


def _platform_base_url() -> str:
    return os.environ.get("PLATFORM_CORE_URL", "http://localhost:8000")


class PlatformClient:
    """
    platform-core の内部 API を呼ぶ非同期 HTTP クライアント。

    async with PlatformClient() as client:
        await client.register_module(module)
    """

    def __init__(self, base_url: str | None = None, timeout: float = 5.0) -> None:
        from platform_core.config import settings
        from platform_core.module_sdk.internal_auth import get_internal_auth_header

        self._base_url = base_url or _platform_base_url()
        self._timeout = timeout
        self._headers = {
            "Content-Type": "application/json",
            **get_internal_auth_header(),
        }
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> PlatformClient:
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers,
            timeout=self._timeout,
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()

    # ── Module Registration ──────────────────────────────────────────

    async def register_module(self, module: "ModuleInfo") -> dict:
        """モジュールを platform-core に upsert 登録する。"""
        from platform_core.module_sdk.base import ModuleInfo

        payload = {
            "key": module.key,
            "name": module.name,
            "description": module.description,
            "base_url": module.base_url,
            "version": module.version,
            "capabilities": module.capabilities,
            "data_contracts": module.data_contracts,
            "health_check_path": module.health_check_path,
        }
        assert self._client is not None
        resp = await self._client.post("/internal/modules/register", json=payload)
        resp.raise_for_status()
        return resp.json()

    # ── Cross-module Calls ───────────────────────────────────────────

    async def call_module(
        self,
        module_key: str,
        path: str,
        method: str = "GET",
        json: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """
        ModuleRegistry に登録されたモジュールのエンドポイントを呼ぶ。

        platform-core がプロキシするのではなく、
        モジュール URL を取得して直接呼ぶ形（開発時シンプル版）。
        """
        module_url = await self._resolve_module_url(module_key)
        async with httpx.AsyncClient(
            base_url=module_url,
            headers=self._headers,
            timeout=self._timeout,
        ) as direct_client:
            resp = await direct_client.request(
                method=method,
                url=path,
                json=json,
                params=params,
            )
            resp.raise_for_status()
            return resp.json()

    async def _resolve_module_url(self, module_key: str) -> str:
        """platform-core から登録済みモジュールの base_url を取得する。"""
        assert self._client is not None
        resp = await self._client.get(f"/internal/modules/{module_key}")
        resp.raise_for_status()
        return resp.json()["base_url"]

    # ── Health Check ─────────────────────────────────────────────────

    async def ping_platform(self) -> bool:
        """platform-core が起動しているか確認する。"""
        try:
            assert self._client is not None
            resp = await self._client.get("/health")
            return resp.is_success
        except Exception:
            return False
