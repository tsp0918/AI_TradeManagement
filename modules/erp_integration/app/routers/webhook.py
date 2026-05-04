"""POST /internal/push-judgment — AI_TM 判定結果を ERP にプッシュする内部エンドポイント。

AI_TM 側の判定が更新された際に呼び出し、ERP の /gts/webhook/judgment-updated に転送する。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..adapters import platform_core as pc_adapter
from ..auth import verify_api_key

router = APIRouter(prefix="/internal", tags=["Internal Webhook"])


class JudgmentPushRequest(BaseModel):
    material_code: str
    new_judgment: str           # APPLICABLE / NOT_APPLICABLE / PENDING
    new_eccn: str | None = None
    rationale: str | None = None


class JudgmentPushResponse(BaseModel):
    success: bool
    material_code: str
    message: str | None = None


@router.post("/push-judgment", response_model=JudgmentPushResponse)
async def push_judgment(
    body: JudgmentPushRequest,
    _: None = Depends(verify_api_key),
) -> JudgmentPushResponse:
    """
    AI_TM の判定結果を ERP の /gts/webhook/judgment-updated に転送する。
    platform-core の判定更新時やバッチ再判定後に呼び出す。
    """
    ok = await pc_adapter.push_judgment_to_erp(
        material_code=body.material_code,
        new_judgment=body.new_judgment,
        new_eccn=body.new_eccn,
        rationale=body.rationale,
    )
    return JudgmentPushResponse(
        success=ok,
        material_code=body.material_code,
        message="Delivered to ERP." if ok else "ERP webhook unreachable; will not retry.",
    )
