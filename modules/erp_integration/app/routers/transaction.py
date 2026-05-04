"""POST /transaction/review, POST /shipment/rescreen — 取引審査・出荷再スクリーニング。

ERP が取引伝票・出荷伝票の審査を依頼するエンドポイント。
platform-core の /api/transaction-reviews/* に委譲する。
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import verify_api_key
from ..config import settings

router = APIRouter(tags=["Transaction & Shipment"])


# ── スキーマ ──────────────────────────────────────────────────────────────────

class TransactionReviewRequest(BaseModel):
    erp_transaction_id: str
    item_code: str
    item_name: str
    item_description: str | None = None
    hs_code: str | None = None
    eccn: str | None = None
    counterparty_name: str
    counterparty_country: str
    counterparty_address: str | None = None
    destination_country: str
    quantity: float | None = None
    value_usd: float | None = None


class TransactionReviewResponse(BaseModel):
    review_id: str
    erp_transaction_id: str
    judgment: str
    review_level: str
    review_completed: bool
    approved: bool
    linked_existing: bool
    eccn: str | None = None
    message: str | None = None


class ShipmentRescreenRequest(BaseModel):
    review_id: str
    erp_shipment_id: str


class ShipmentRescreenResponse(BaseModel):
    review_id: str
    erp_shipment_id: str
    approved: bool
    rescreen_changed: bool
    judgment: str
    message: str | None = None


# ── エンドポイント ─────────────────────────────────────────────────────────────

@router.post("/transaction/review", response_model=TransactionReviewResponse)
async def transaction_review(
    body: TransactionReviewRequest,
    _: None = Depends(verify_api_key),
) -> TransactionReviewResponse:
    """
    ERP 取引伝票の輸出審査を依頼する。
    ① 既存の有効な審査がある場合: 紐づけて即座に結果を返す。
    ② 新規の場合: 品目・取引先を登録し、AI 該非判定 + スクリーニングを実行して返す。
    """
    try:
        async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.PLATFORM_CORE_URL}/api/transaction-reviews/check-and-link",
                json=body.model_dump(),
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code,
                            detail=f"platform-core error: {exc.response.text}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"platform-core unreachable: {exc}") from exc

    return TransactionReviewResponse(**data)


@router.post("/shipment/rescreen", response_model=ShipmentRescreenResponse)
async def shipment_rescreen(
    body: ShipmentRescreenRequest,
    _: None = Depends(verify_api_key),
) -> ShipmentRescreenResponse:
    """
    ERP 出荷伝票の再スクリーニングを依頼する。
    ③ 紐づいた審査レコードで再スクリーニングを実行し、変化の有無で出荷 OK/NG を返す。
    """
    try:
        async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.PLATFORM_CORE_URL}/api/transaction-reviews/{body.review_id}/shipment-rescreen",
                json={"erp_shipment_id": body.erp_shipment_id},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code,
                            detail=f"platform-core error: {exc.response.text}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"platform-core unreachable: {exc}") from exc

    return ShipmentRescreenResponse(**data)
