"""POST /hs/classify — ERP → HS コード分類。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..adapters import hs_classifier
from ..auth import verify_api_key
from ..schemas import HSClassifyRequest, HSClassifyResponse

router = APIRouter(prefix="/hs", tags=["HS Classification"])


@router.post("/classify", response_model=HSClassifyResponse)
async def classify_hs(
    body: HSClassifyRequest,
    _: None = Depends(verify_api_key),
) -> HSClassifyResponse:
    """品目説明から HS コードを分類する。"""
    try:
        result = await hs_classifier.classify_sync(body.description, body.material_code)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"HS classifier error: {exc}") from exc

    return HSClassifyResponse(
        hs_code=result["hs_code"],
        confidence=result["confidence"],
        rationale=result.get("rationale"),
    )
