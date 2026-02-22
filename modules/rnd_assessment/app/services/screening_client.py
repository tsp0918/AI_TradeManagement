from typing import Any, Dict
import httpx

from app.core.config import settings


class ScreeningClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or settings.screening_service_base_url

    async def submit_screening_job(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/screenings"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(url, json=payload)
                r.raise_for_status()
                return r.json()
        except Exception:
            # PoCスタブ（transaction_id を必ず返す）
            return {"transaction_id": -1, "status": "stubbed", "payload_echo": payload}
