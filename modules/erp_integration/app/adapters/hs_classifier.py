"""hs_classifier (port 8006) への呼び出しアダプター。"""
from __future__ import annotations

import uuid

import httpx

from ..config import settings


async def classify_sync(description: str, material_code: str | None) -> dict:
    """
    POST http://localhost:8006/classify/sync → ClassifyResult
    Returns: {"hs_code": str, "confidence": float, "rationale": str}
    """
    item_id = material_code or str(uuid.uuid4())
    payload = {
        "item_id": item_id,
        "item_name": material_code or description[:50],
        "item_description": description,
        "top_k": 3,
    }
    async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
        resp = await client.post(f"{settings.HS_CLASSIFIER_URL}/classify/sync", json=payload)
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results") or []
    if not results:
        return {"hs_code": "UNKNOWN", "confidence": 0.0, "rationale": "No candidates found"}

    top = results[0]
    return {
        "hs_code": top.get("hs_code", "UNKNOWN"),
        "confidence": float(top.get("similarity_score") or top.get("score") or 0.0),
        "rationale": top.get("description_en") or top.get("description") or "",
    }
