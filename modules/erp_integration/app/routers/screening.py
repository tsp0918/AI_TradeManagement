"""POST /screening/denied-party — 制裁リストスクリーニング。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..adapters import screening as screening_adapter
from ..auth import verify_api_key
from ..schemas import DeniedPartyRequest, DeniedPartyResponse

router = APIRouter(prefix="/screening", tags=["Screening"])


@router.post("/denied-party", response_model=DeniedPartyResponse)
async def check_denied_party(
    body: DeniedPartyRequest,
    _: None = Depends(verify_api_key),
) -> DeniedPartyResponse:
    """取引先名を制裁リスト（OFAC/BIS/EU/UK）に照合する。"""
    try:
        result = await screening_adapter.screen_entity(body.name, body.country, body.address)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Screening service error: {exc}") from exc

    return DeniedPartyResponse(
        is_match=result["is_match"],
        list_name=result.get("list_name"),
        confidence=result["confidence"],
        rationale=result.get("rationale"),
    )
