"""POST /classify — HS コード判定（非同期）、POST /classify/sync — 同期判定、GET /index/status"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel, Field

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


class ClassifySyncRequest(BaseModel):
    """同期判定リクエスト（callback_url 不要）。"""
    item_id: str
    item_name: str
    item_description: str
    additional_keywords: list[str] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=1, le=20)

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


@router.post("/classify/sync", response_model=ClassifyResult)
async def classify_item_sync(request: ClassifySyncRequest):
    """品目情報を受け取り、HSコード判定を同期実行して結果を直接返す（webhook 不要）。"""
    if not faiss_index.is_built():
        raise HTTPException(
            status_code=503,
            detail="Layer C インデックスが未ロードです。サービス起動直後は少し待ってから再試行してください。",
        )
    req_id = str(uuid.uuid4())
    # ClassifyRequest 互換オブジェクトを構築（callback_url はダミー）
    cr = ClassifyRequest(
        request_id=req_id,
        item_id=request.item_id,
        item_name=request.item_name,
        item_description=request.item_description,
        additional_keywords=request.additional_keywords,
        top_k=request.top_k,
        callback_url="http://localhost/noop",
    )
    try:
        candidates = classifier.classify(cr)
        return ClassifyResult(
            request_id=req_id,
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
        logger.exception("同期判定エラー: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/index/status", response_model=IndexStatus)
def index_status():
    """インデックスの現在状態を返す。"""
    from platform_core.services.faiss_e5_service import get_staging_dir
    staging = get_staging_dir()
    return IndexStatus(
        built=faiss_index.is_built(),
        index_size=faiss_index.ntotal(),
        model=_MODEL_NAME,
        cache_exists=(staging / "layer_c.index").exists(),
        hs_data_exists=(staging / "layer_c_meta.json").exists(),
    )
