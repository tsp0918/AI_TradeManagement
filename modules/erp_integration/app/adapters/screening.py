"""screening (port 8005) への呼び出しアダプター。"""
from __future__ import annotations

import httpx

from ..config import settings

# 絶対禁輸国（EAR/外為法）
_EMBARGOED_COUNTRIES = {"IR", "KP", "RU", "BY", "SY", "CU", "SD", "MM"}


async def screen_entity(name: str, country: str | None, address: str | None) -> dict:
    """
    POST http://localhost:8005/api/screen
    Returns: {"is_match": bool, "list_name": str|None, "confidence": float, "rationale": str}
    """
    payload: dict = {"company_name": name, "threshold": 0.75}
    if country:
        payload["country"] = country
    if address:
        payload["address"] = address

    async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
        resp = await client.post(f"{settings.SCREENING_URL}/api/screen", json=payload)
        resp.raise_for_status()
        data = resp.json()

    result_status = data.get("result_status", "clear")
    # screening returns "match" / "possible_match" / "clear" (not "hit")
    is_match = result_status in ("match", "possible_match")
    matches = data.get("matches", [])
    max_score = data.get("max_score") or 0.0
    list_name = matches[0].get("list_source") if matches else None

    # 禁輸国チェック
    if country and country.upper() in _EMBARGOED_COUNTRIES:
        is_match = True
        list_name = list_name or f"EMBARGOED_COUNTRY:{country.upper()}"
        max_score = max(max_score, 1.0)

    rationale = f"Screening status: {result_status}"
    if matches:
        rationale += f" | Matched: {matches[0].get('entity_name','')}"

    return {
        "is_match": is_match,
        "list_name": list_name,
        "confidence": float(max_score),
        "rationale": rationale,
    }
