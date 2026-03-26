"""特許出願人（Assignee）制裁リスト一括照合エンドポイント。

POST /api/screen-applicants
  body: {"names": ["Company A", "Company B", ...]}
  → screening モジュールの POST /api/screen を名前ごとに呼び出し結果を返す
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()

_SCREENING_URL = os.environ.get("MODULE_SCREENING_URL", "http://localhost:8005")


class ApplicantScreenRequest(BaseModel):
    names: list[str] = Field(..., min_length=1, description="スクリーニング対象の出願人名リスト")
    threshold: float = Field(0.75, ge=0.0, le=1.0)


class ApplicantScreenResult(BaseModel):
    name: str
    result_status: str          # "clear" | "possible_match" | "match" | "error"
    max_score: float
    top_match: str | None = None
    screening_id: str | None = None


@router.post("/screen-applicants", response_model=list[ApplicantScreenResult])
async def screen_applicants(req: ApplicantScreenRequest) -> list[ApplicantScreenResult]:
    """出願人名リストを制裁リストと照合する。重複名は一度だけ照合。"""
    seen: dict[str, ApplicantScreenResult] = {}
    unique_names = list(dict.fromkeys(n.strip() for n in req.names if n.strip()))

    async with httpx.AsyncClient(timeout=10.0) as client:
        for name in unique_names:
            if name in seen:
                continue
            try:
                resp = await client.post(
                    f"{_SCREENING_URL}/api/screen",
                    json={"company_name": name, "threshold": req.threshold},
                )
                if resp.status_code == 200:
                    data: dict[str, Any] = resp.json()
                    top = data.get("matches", [])
                    seen[name] = ApplicantScreenResult(
                        name=name,
                        result_status=data.get("result_status", "error"),
                        max_score=data.get("max_score") or 0.0,
                        top_match=top[0]["matched_name"] if top else None,
                        screening_id=str(data["id"]) if data.get("id") else None,
                    )
                else:
                    logger.warning("[screen_applicants] screening returned %s for %r", resp.status_code, name)
                    seen[name] = ApplicantScreenResult(name=name, result_status="error", max_score=0.0)
            except Exception as e:
                logger.error("[screen_applicants] error screening %r: %s", name, e)
                seen[name] = ApplicantScreenResult(name=name, result_status="error", max_score=0.0)

    return list(seen.values())
