# app/services/external_app_client.py
from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from ..settings import settings


class ExternalAppClient:
    def __init__(self):
        self.base_url = settings.EXTERNAL_APP_BASE_URL.rstrip("/")
        self.timeout = settings.EXTERNAL_APP_TIMEOUT_SEC

    async def request_export_control(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        外部アプリへ「判定依頼」をPOST

        外部AI（AI_validation）側の現行仕様:
          POST /export-control/requests

        ※ 旧仕様 (/api/v1/export-control/requests) は叩かない
        """
        url = f"{self.base_url}/export-control/requests"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            return r.json()
