from typing import Any, Dict
import httpx

from app.core.config import settings


class ItemClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or settings.item_service_base_url

    async def fetch_item(self, external_item_id: str) -> Dict[str, Any]:
        url = f"{self.base_url}/items/{external_item_id}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(url)
                r.raise_for_status()
                return r.json()
        except Exception:
            # PoC継続のためのスタブ（後で実仕様に置換）
            return {
                "external_item_id": external_item_id,
                "name": f"ITEM({external_item_id})",
                "description": None,
                "hs_code": None,
                "eccn_candidate": None,
                "export_control_flags": [],
            }
