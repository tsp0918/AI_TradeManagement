"""POST /classify — HS コード判定（非同期）、POST /index/rebuild"""
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
                embedding_model=settings.embedding_model_name,
                dataset_version="HS2022",
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
    if not faiss_index._is_built():
        raise HTTPException(
            status_code=503,
            detail="FAISSインデックスが未構築です。HS データファイルを data/ に配置してサービスを再起動してください。",
        )
    background_tasks.add_task(_run_classification, request)
    return ClassifyAccepted(
        request_id=request.request_id,
        message=f"Classification task queued (index_size={faiss_index.ntotal()})",
    )


@router.post("/index/rebuild", status_code=status.HTTP_200_OK)
def rebuild_index():
    """FAISS インデックスを強制再構築する（Embedding キャッシュも再計算）。"""
    try:
        faiss_index.build(force=True)
        return {
            "ok": True,
            "ntotal": faiss_index.ntotal(),
            "message": f"インデックス再構築完了: {faiss_index.ntotal()} 件",
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/index/status", response_model=IndexStatus)
def index_status():
    """インデックスの現在状態を返す。"""
    from pathlib import Path
    return IndexStatus(
        built=faiss_index._is_built(),
        index_size=faiss_index.ntotal(),
        model=settings.embedding_model_name,
        cache_exists=Path(settings.embedding_cache_path).exists(),
        hs_data_exists=Path(settings.hs_data_path).exists(),
    )
