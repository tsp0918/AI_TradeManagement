# app/services/ollama_client.py
from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx


class OllamaClient:
    """
    Ollama /api/chat を叩いて JSON レスポンスを返す薄いクライアント。
    - timeout を明示的に指定して ReadTimeout を防ぐ
    - base_url / model は環境変数から取得
    """

    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None):
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL") or "llama3.2:3b"

    async def chat_json(
        self,
        prompt: str,
        timeout_sec: float = 600.0,
        *,
        temperature: float = 0.0,
        num_ctx: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        stream=False で一括応答。Ollamaの返すJSON(dict)をそのまま返す。
        """
        url = f"{self.base_url}/api/chat"

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {
                "temperature": temperature,
            },
        }

        # 追加オプション（必要なら）
        if num_ctx is not None:
            payload["options"]["num_ctx"] = int(num_ctx)

        # ★重要：timeout を httpx.Timeout で明示（read/write含む）
        timeout = httpx.Timeout(
            timeout_sec,
            connect=10.0,
            read=timeout_sec,
            write=timeout_sec,
            pool=timeout_sec,
        )

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(url, json=payload)
                r.raise_for_status()
                return r.json()
        except httpx.ConnectError as e:
            raise RuntimeError(
                f"Ollamaに接続できません: {self.base_url} （ollama serve が起動しているか確認）"
            ) from e
        except httpx.ReadTimeout as e:
            raise RuntimeError(
                f"Ollama応答がタイムアウトしました（{timeout_sec}s）。"
                f" CPU推論の場合は timeout_sec を延長するか、入力を短くしてください。"
            ) from e
        except httpx.HTTPStatusError as e:
            # Ollamaが500などを返した場合
            body = ""
            try:
                body = e.response.text[:2000]
            except Exception:
                body = ""
            raise RuntimeError(f"OllamaがHTTPエラーを返しました: {e}. body={body}") from e
