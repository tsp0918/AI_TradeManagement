"""platform-core (port 8000) へのデータ書き込みアダプター。

品目同期・輸出許可申請ドラフト作成・ERP コールバックを担う。
"""
from __future__ import annotations

import logging

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


async def upsert_item(
    material_code: str,
    name: str,
    description: str | None,
    eccn: str | None,
    hs_code: str | None,
    judgment: str | None = None,
) -> str | None:
    """
    ERP 品目を platform-core の plat_item に upsert する。
    Returns: item UUID (str) or None on failure.
    """
    status_map = {
        "APPLICABLE": "controlled",
        "NOT_APPLICABLE": "non_controlled",
        "PENDING": "pending",
    }
    payload: dict = {
        "item_code": material_code,
        "name": name or material_code,
    }
    if description:
        payload["description"] = description
    if eccn:
        payload["eccn"] = eccn
    if hs_code:
        payload["hs_code"] = hs_code
    if judgment:
        payload["export_control_status"] = status_map.get(judgment, "pending")

    try:
        async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.PLATFORM_CORE_URL}/api/item-versions/items",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json().get("id")
    except Exception as exc:
        logger.warning("upsert_item failed for %s: %s", material_code, exc)
        return None


async def create_export_application(
    reference: str,
    destination_country: str,
    consignee_name: str,
    item_description: str,
    eccn: str | None,
    decision: str,
) -> str | None:
    """
    輸出許可申請ドラフトを platform-core に作成する。
    Returns: application UUID (str) or None on failure.
    """
    payload = {
        "license_type": "EAR",
        "item_description": item_description,
        "destination_country": destination_country,
        "consignee_name": consignee_name,
        "transaction_ids": [reference],
        "notes": f"Auto-created from ERP export precheck. Decision: {decision}",
    }
    if eccn:
        payload["eccn"] = eccn

    try:
        async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.PLATFORM_CORE_URL}/api/export-licenses",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            app_id = data.get("id")
            logger.info("Export application created: %s (ref=%s)", app_id, reference)
            return app_id
    except Exception as exc:
        logger.warning("create_export_application failed for ref=%s: %s", reference, exc)
        return None


async def push_judgment_to_erp(
    material_code: str,
    new_judgment: str,
    new_eccn: str | None,
    rationale: str | None,
) -> bool:
    """
    AI_TM の判定結果を ERP の /gts/webhook/judgment-updated に通知する。
    Returns True on success.
    """
    payload = {
        "material_code": material_code,
        "new_judgment": new_judgment,
        "new_eccn": new_eccn or "",
        "rationale": rationale or "",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.ERP_WEBHOOK_URL}/gts/webhook/judgment-updated",
                json=payload,
                headers={"Authorization": f"Bearer {settings.ERP_WEBHOOK_API_KEY}"},
            )
            resp.raise_for_status()
            logger.info("Pushed judgment to ERP for %s: %s", material_code, new_judgment)
            return True
    except Exception as exc:
        logger.warning("push_judgment_to_erp failed for %s: %s", material_code, exc)
        return False
