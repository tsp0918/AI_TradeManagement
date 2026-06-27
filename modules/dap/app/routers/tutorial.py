"""DAP チュートリアル API — ステップ案内 + step-aware LLM assist

トークン効率設計:
  Tier 1 (0 tok)   : FAQ インメモリキャッシュ
  Tier 2 (0 API)   : Ollama ローカル (単純操作質問, max 150 tok)
  Tier 3 (~560 tok) : Claude (規制・コンプライアンス判断, max 350 tok)
  ※ 通常チャット比で ~65% 削減
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tutorial", tags=["tutorial"])

_UC_DEFS: list[dict] = json.loads(
    (Path(__file__).parents[1] / "uc_definitions.json").read_text(encoding="utf-8")
)
_UC_MAP: dict[str, dict] = {uc["id"]: uc for uc in _UC_DEFS}

# ── インメモリ FAQ キャッシュ (プロセス lifetime) ─────────────────────────────
_faq_cache: dict[str, str] = {}
_FAQ_MAX = 300


def _q_hash(uc_id: str, step_num: int, message: str) -> str:
    key = f"{uc_id}:{step_num}:{message.strip().lower()}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


# ── スキーマ ─────────────────────────────────────────────────────────────────

class TutorialAssistRequest(BaseModel):
    session_id: str = "anon"
    uc_id: str
    step_num: int
    message: str


# ── エンドポイント ────────────────────────────────────────────────────────────

@router.get("/list")
def list_tutorials() -> list[dict]:
    """全 UC チュートリアルの概要一覧。"""
    return [
        {
            "id": uc["id"],
            "title": uc["title"],
            "persona": uc.get("persona", ""),
            "description": uc.get("description", ""),
            "step_count": len(uc.get("steps", [])),
        }
        for uc in _UC_DEFS
    ]


@router.get("/{uc_id}")
def get_tutorial(uc_id: str) -> dict:
    """指定 UC の全ステップ情報を返す。"""
    uc = _UC_MAP.get(uc_id)
    if not uc:
        raise HTTPException(404, detail=f"UC not found: {uc_id}")
    return uc


@router.post("/assist")
async def tutorial_assist(req: TutorialAssistRequest) -> dict:
    """
    チュートリアル中の Q&A アシスト。

    優先順: FAQ キャッシュ → Ollama (単純質問) → Claude (規制判断)
    """
    uc   = _UC_MAP.get(req.uc_id)
    step = next((s for s in (uc or {}).get("steps", []) if s["num"] == req.step_num), None)

    # Tier 1: FAQ キャッシュ
    faq_key = _q_hash(req.uc_id, req.step_num, req.message)
    if faq_key in _faq_cache:
        logger.info("[tutorial/assist] cache hit %s", faq_key)
        return {"reply": _faq_cache[faq_key], "source": "cache"}

    step_ctx = _build_step_context(uc, step)

    # Tier 2/3: LLM ルーティング
    from ..llm_router import route_request
    route = route_request(req.message)

    reply = ""
    source = route
    try:
        if route == "local":
            from ..llm_router import generate_tutorial_local
            reply = await generate_tutorial_local(req.message, step_ctx)
        else:
            from ..llm_router import generate_tutorial_sonnet
            reply = await generate_tutorial_sonnet(req.message, step_ctx)
    except Exception as e:
        logger.warning("[tutorial/assist] %s failed, fallback to sonnet: %s", route, e)
        source = "sonnet"
        try:
            from ..llm_router import generate_tutorial_sonnet
            reply = await generate_tutorial_sonnet(req.message, step_ctx)
        except Exception as e2:
            reply = f"申し訳ありません。現在回答を生成できません（{type(e2).__name__}）"
            source = "error"

    # Tier 2 の回答のみキャッシュ（操作説明は汎化しやすい）
    if source == "local" and reply and len(_faq_cache) < _FAQ_MAX:
        _faq_cache[faq_key] = reply

    return {"reply": reply, "source": source}


@router.delete("/assist/cache")
def clear_faq_cache() -> dict:
    """FAQ キャッシュをリセットする（デバッグ用）。"""
    _faq_cache.clear()
    return {"cleared": True}


# ── ヘルパー ─────────────────────────────────────────────────────────────────

def _build_step_context(uc: Optional[dict], step: Optional[dict]) -> str:
    """LLM に渡す圧縮ステップコンテキスト（full page state を避けてトークン節約）。"""
    if not uc:
        return "チュートリアル情報なし"
    total = len(uc.get("steps", []))
    num   = step["num"] if step else "?"
    parts = [f"チュートリアル「{uc['title']}」Step {num}/{total}"]
    if step:
        parts.append(f"操作: {step['title']}")
        if step.get("detail"):
            parts.append(f"説明: {step['detail'][:200]}")
        gs      = step.get("guidance_steps", [])
        targets = [g.get("target", "") for g in gs if g.get("target")]
        if targets:
            parts.append(f"対象要素: {', '.join(targets[:4])}")
    return "\n".join(parts)
