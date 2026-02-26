"""HSコード判定モジュール FastAPI アプリ。

エンドポイント:
  GET  /health              ヘルスチェック
  GET  /ui                  HSコード検索 UI (HTML)
  POST /classify            HSコード判定（非同期 + webhook）
  GET  /search              セマンティック検索（同期）
  GET  /hs/{hs_code}        HSコード詳細
  POST /index/rebuild       FAISSインデックス強制再構築
  GET  /index/status        インデックス状態確認
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

# HuggingFace tokenizer のマルチプロセスを無効化（semaphore リークによるプロセス終了を防ぐ）
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from platform_core.module_sdk import AuditMiddleware, ModuleInfo, build_lifespan

from app.config import settings
from app.routers import classify, search, ui
from app.services import faiss_index

logger = logging.getLogger(__name__)


async def _init_index() -> None:
    """起動時に FAISS インデックスを構築する。データがなければスキップ。"""
    hs_path = Path(settings.hs_data_path)
    if not hs_path.exists():
        logger.warning(
            "HS データファイルが見つかりません: %s\n"
            "data/ ディレクトリに hs2022_6digit.json を配置してサービスを再起動してください。",
            hs_path,
        )
        return
    try:
        faiss_index.build(force=False)
    except Exception as exc:
        logger.error("FAISSインデックス構築に失敗しました: %s", exc)


MODULE = ModuleInfo(
    key="hs_classifier",
    name="HSコード判定",
    base_url=os.environ.get("MODULE_HS_CLASSIFIER_URL", f"http://localhost:{settings.port}"),
    description="品目管理と連携し、HS2022 6桁コードを FAISS セマンティック検索で自動付番支援する",
    capabilities=["hs_classify", "hs_search"],
    data_contracts={
        "input":  ["Product"],
        "output": ["HSCandidate"],
    },
)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=build_lifespan(MODULE, on_startup=_init_index),
    )

    app.add_middleware(AuditMiddleware, module_key="hs_classifier")

    app.include_router(classify.router)
    app.include_router(search.router)
    app.include_router(ui.router)

    @app.get("/", include_in_schema=False)
    def _root():
        return RedirectResponse(url="/ui")

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "index_size": faiss_index.ntotal(),
            "index_built": faiss_index._is_built(),
        }

    return app


app = create_app()
