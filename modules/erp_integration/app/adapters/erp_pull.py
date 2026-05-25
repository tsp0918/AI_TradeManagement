"""ERP マスターデータ プル型アダプター（AI_TM → ERP 方向）。

AI Trade Management 側から ERP に問い合わせて品目・取引先の
最新マスターデータを取得する。ERP 側 API の想定スキーマ:

  GET {ERP_API_URL}/api/materials/{code}
  GET {ERP_API_URL}/api/materials?export_review=true&limit=N
  GET {ERP_API_URL}/api/vendors/{code}
  GET {ERP_API_URL}/api/vendors?limit=N
"""
from __future__ import annotations

import logging

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.ERP_API_KEY}"}


async def pull_item(erp_item_code: str) -> dict | None:
    """ERP から単一品目マスターをコードで取得する。接続失敗・404 は None を返す。"""
    try:
        async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
            resp = await client.get(
                f"{settings.ERP_API_URL}/api/materials/{erp_item_code}",
                headers=_auth_headers(),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning("pull_item failed for %s: %s", erp_item_code, exc)
        return None


async def pull_items_for_review(limit: int = 200) -> list[dict]:
    """輸出管理レビュー対象品目リストを ERP から一括取得する。"""
    try:
        async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
            resp = await client.get(
                f"{settings.ERP_API_URL}/api/materials",
                params={"export_review": "true", "limit": limit},
                headers=_auth_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else data.get("items", data.get("materials", []))
    except Exception as exc:
        logger.warning("pull_items_for_review failed: %s", exc)
        return []


async def pull_counterparty(erp_party_code: str) -> dict | None:
    """ERP から単一取引先マスターをコードで取得する。接続失敗・404 は None を返す。"""
    try:
        async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
            resp = await client.get(
                f"{settings.ERP_API_URL}/api/vendors/{erp_party_code}",
                headers=_auth_headers(),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning("pull_counterparty failed for %s: %s", erp_party_code, exc)
        return None


async def pull_counterparties_for_review(limit: int = 200) -> list[dict]:
    """スクリーニング対象取引先リストを ERP から一括取得する。"""
    try:
        async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
            resp = await client.get(
                f"{settings.ERP_API_URL}/api/vendors",
                params={"limit": limit},
                headers=_auth_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else data.get("vendors", data.get("items", []))
    except Exception as exc:
        logger.warning("pull_counterparties_for_review failed: %s", exc)
        return []
