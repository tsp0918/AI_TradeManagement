"""ai_classification モジュールへの品目同期アダプター。"""
from __future__ import annotations

import httpx

from ..config import settings

_JUDGMENT_TO_STATUS = {
    "APPLICABLE":     "controlled",
    "NOT_APPLICABLE": "non_controlled",
    "PENDING":        "needs_attention",
    "NEEDS_REVIEW":   "needs_attention",
}


async def sync_to_classification(
    *,
    material_code: str,
    name: str,
    description: str = "",
    hs_code: str | None = None,
    eccn: str | None = None,
    country_of_origin: str | None = None,
    item_type: str = "FINISHED_GOODS",
    judgment: str | None = None,
    rationale: str | None = None,
) -> None:
    """ai_classification の品目DB に ERP 品目を upsert する（失敗は無視）。"""
    payload = {
        "code": material_code,
        "name": name,
        "description": description,
        "hs_code": hs_code,
        "eccn": eccn,
        "country_of_origin": country_of_origin,
        "item_type": item_type,
        "export_control_status": _JUDGMENT_TO_STATUS.get(judgment or "") if judgment else None,
        "export_control_reason": rationale,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
            await client.post(
                f"{settings.AI_CLASSIFICATION_URL}/products/erp-sync",
                json=payload,
            )
    except Exception:
        pass
