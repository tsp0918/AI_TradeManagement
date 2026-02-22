# app/services/external_client.py
from __future__ import annotations

import os
from typing import Any, Dict, Optional
import httpx


class ExternalAIClient:
    """
    外部解析アプリとの通信用クライアント
    想定:
      - POST {BASE_URL}/api/v1/jobs   -> { job_id: "...", status: "queued" }
      - GET  {BASE_URL}/api/v1/jobs/{job_id} -> { status, result: {...} }
      - （任意）外部がこちらの webhook に結果をPOSTしてくる
    """

    def __init__(self):
        self.base_url = os.getenv("EXTERNAL_AI_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
        self.api_key = os.getenv("EXTERNAL_AI_API_KEY", "")
        self.timeout_sec = float(os.getenv("EXTERNAL_AI_TIMEOUT_SEC", "15"))

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def create_job(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/api/v1/jobs"
        async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
            r = await client.post(url, json=payload, headers=self._headers())
            r.raise_for_status()
            return r.json()

    async def get_job(self, job_id: str) -> Dict[str, Any]:
        url = f"{self.base_url}/api/v1/jobs/{job_id}"
        async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
            r = await client.get(url, headers=self._headers())
            r.raise_for_status()
            return r.json()
