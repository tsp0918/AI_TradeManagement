"""
module_sdk.base
===============
各モジュールが起動時に呼ぶ登録ヘルパー。

使い方（モジュールの main.py）:
    from platform_core.module_sdk import ModuleInfo, build_lifespan

    MODULE = ModuleInfo(
        key="ai_validation",
        name="AI該非判定",
        base_url="http://localhost:8011",
        capabilities=["export_control", "matrix_run"],
        data_contracts={
            "input":  ["Transaction"],
            "output": ["AiRun", "MatrixMatch"],
        },
    )

    app = FastAPI(lifespan=build_lifespan(MODULE))
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import FastAPI


@dataclass
class ModuleInfo:
    """モジュール自己申告情報。platform-core の ModuleRegistry に登録される。"""

    key: str
    name: str
    base_url: str
    description: str = ""
    version: str = "0.1.0"
    capabilities: list[str] = field(default_factory=list)
    data_contracts: dict | None = None
    health_check_path: str = "/health"


def build_lifespan(
    module: ModuleInfo,
    on_startup: "Callable[[], Awaitable[None]] | None" = None,
    on_shutdown: "Callable[[], Awaitable[None]] | None" = None,
):
    """
    FastAPI の lifespan コンテキストマネージャを生成する。

    起動時:
      1. platform-core へ自動登録（失敗しても起動継続）
      2. on_startup コールバック（DB初期化など）

    終了時:
      1. on_shutdown コールバック（DBクローズなど）

    使い方（既存の lifespan がある場合）:
        app = FastAPI(
            lifespan=build_lifespan(MODULE, on_startup=init_db, on_shutdown=close_db)
        )
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        import logging
        from platform_core.module_sdk.client import PlatformClient

        logger = logging.getLogger(__name__)

        # 1. platform-core へ登録
        try:
            async with PlatformClient() as client:
                await client.register_module(module)
            logger.info("Registered module '%s' with platform-core.", module.key)
        except Exception as exc:
            logger.warning(
                "Could not register module '%s' with platform-core: %s. "
                "Continuing startup without registration.",
                module.key,
                exc,
            )

        # 2. モジュール固有の起動処理
        if on_startup is not None:
            await on_startup()

        yield

        # 3. モジュール固有の終了処理
        if on_shutdown is not None:
            await on_shutdown()

    return lifespan
