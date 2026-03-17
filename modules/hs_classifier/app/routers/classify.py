"""POST /classify — HS コード判定（非同期）、GET /index/status"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from app.config import settings
from app.models.schemas import (
    ClassifyAccepted,
    ClassifyRequest,
    ClassifyResult,
    IndexStatus,
    ModelInfo,
)
from app.services import classifier, faiss_index, webhook

logger = logging.getLogger(__name__)
router = APIRouter(tags=["classify"])

_MODEL_NAME = "intfloat/multilingual-e5-large"
_DATASET_VERSION = "HS2022 (Layer C / e5-large)"


async def _run_classification(request: ClassifyRequest) -> None:
    """バックグラウンドで判定を実行し、webhook に結果を返す。"""
    try:
        candidates = classifier.classify(request)
        payload = ClassifyResult(
            request_id=request.request_id,
            item_id=request.item_id,
            status="completed",
            classified_at=datetime.now(timezone.utc),
            results=candidates,
            model_info=ModelInfo(
                embedding_model=_MODEL_NAME,
                dataset_version=_DATASET_VERSION,
                index_size=faiss_index.ntotal(),
            ),
        )
    except Exception as exc:
        logger.exception("判定エラー: %s", exc)
        payload = ClassifyResult(
            request_id=request.request_id,
            item_id=request.item_id,
            status="failed",
            error=str(exc),
            failed_at=datetime.now(timezone.utc),
        )

    await webhook.send_webhook(request.callback_url, payload.model_dump(mode="json"))


@router.post("/classify", response_model=ClassifyAccepted, status_code=status.HTTP_202_ACCEPTED)
async def classify_item(request: ClassifyRequest, background_tasks: BackgroundTasks):
    """品目情報を受け取り、HSコード判定をバックグラウンド実行する。完了後 callback_url へ結果を POST する。"""
    if not faiss_index.is_built():
        raise HTTPException(
            status_code=503,
            detail="Layer C インデックスが未ロードです。サービス起動直後は少し待ってから再試行してください。",
        )
    background_tasks.add_task(_run_classification, request)
    return ClassifyAccepted(
        request_id=request.request_id,
        message=f"Classification task queued (index_size={faiss_index.ntotal()})",
    )


@router.get("/index/status", response_model=IndexStatus)
def index_status():
    """インデックスの現在状態を返す。"""
    from pathlib import Path
    from platform_core.services import faiss_e5_service as svc
    staging = svc._staging_dir()
    return IndexStatus(
        built=faiss_index.is_built(),
        index_size=faiss_index.ntotal(),
        model=_MODEL_NAME,
        cache_exists=(staging / "layer_c.index").exists(),
        hs_data_exists=(staging / "layer_c_meta.json").exists(),
    )
