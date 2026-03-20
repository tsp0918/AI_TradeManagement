"""
platform_core.agent.router
============================
Platform Orchestrator Agent の FastAPI エンドポイント。

エンドポイント:
    POST   /agent/sessions                     セッション開始（最初の質問を返す）
    GET    /agent/sessions                     アクティブセッション一覧
    GET    /agent/sessions/{session_id}        セッション状態取得
    POST   /agent/sessions/{session_id}/answer ユーザー回答を送信（次の質問を返す）
    POST   /agent/sessions/{session_id}/judge  最終判定を実行
    DELETE /agent/sessions/{session_id}        セッション破棄

セッション管理:
    plat_agent_session テーブルに永続化する。
    TransactionContext の全状態を context_snapshot (JSON) として保存し、
    candidate_domain_ids・_last_missing_attr も復元可能。
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.agent.hantei_agent import HanteiAgent, create_hantei_agent
from platform_core.agent.tools import default_tools
from platform_core.agent.session_repository import (
    delete_session,
    list_active_sessions,
    load_session,
    mark_judged,
    save_session,
)
from platform_core.db.session import get_db

router = APIRouter(prefix="/agent", tags=["agent"])


# ─────────────────────────────────────────────────
# リクエスト / レスポンス スキーマ
# ─────────────────────────────────────────────────

class StartSessionRequest(BaseModel):
    """セッション開始リクエスト"""
    domain:              str  = Field(default="hantei", description="hantei (現状は hantei のみ)")
    initial_query:       str  = Field(...,               description="最初の自然言語クエリ（品目・取引の説明）")
    transaction_id:      Optional[int]        = Field(None, description="既存 Transaction と紐付ける場合")
    initial_candidates:  Optional[list[str]]  = Field(None, description="FAISS検索済み domain_id（省略時は内部検索）")
    api_key:             Optional[str]        = Field(None, description="Anthropic APIキー（省略時は環境変数）")


class MissingAttrOut(BaseModel):
    attr_key:  str
    label:     str
    reason:    str
    priority:  int
    unit:      Optional[str]
    example:   Optional[str]


class SessionResponse(BaseModel):
    """セッション応答（start / answer 共通）"""
    session_id:              str
    turn:                    int
    question:                Optional[str]
    missing_attr:            Optional[MissingAttrOut]
    candidates_remaining:    list[str]
    candidates_count:        int
    is_ready_for_judgment:   bool
    auto_tool_executed:      Optional[str]
    context_snapshot:        dict


class AnswerRequest(BaseModel):
    answer: str = Field(..., description="ユーザーの回答")


class JudgmentResponse(BaseModel):
    """最終判定応答"""
    session_id:       str
    overall_status:   str
    controlled_items: list[str]
    excluded_items:   list[str]
    pending_items:    list[str]
    reasons:          list[dict]
    summary:          str
    judged_at:        str


# ─────────────────────────────────────────────────
# ヘルパー
# ─────────────────────────────────────────────────

def _agent_response_to_out(session_id: str, resp: Any, turn: int) -> SessionResponse:
    missing_out = None
    if resp.missing_attr:
        ma = resp.missing_attr
        missing_out = MissingAttrOut(
            attr_key=ma.attr_key,
            label=ma.label,
            reason=ma.reason,
            priority=ma.priority,
            unit=ma.unit,
            example=ma.example,
        )
    return SessionResponse(
        session_id=session_id,
        turn=turn,
        question=resp.question,
        missing_attr=missing_out,
        candidates_remaining=resp.candidates_remaining,
        candidates_count=len(resp.candidates_remaining),
        is_ready_for_judgment=resp.is_ready_for_judgment,
        auto_tool_executed=resp.auto_tool_executed,
        context_snapshot=resp.context_snapshot,
    )


def _get_turn(agent: HanteiAgent) -> int:
    ctx = agent.context
    history = getattr(getattr(ctx, "_ctx", ctx), "dialogue_history", [])
    return len(history)


async def _load_or_404(
    db: AsyncSession,
    session_id: str,
    api_key: Optional[str] = None,
) -> HanteiAgent:
    """セッションをDBから復元し、見つからなければ 404 を返す"""
    agent = await load_session(db, session_id, api_key=api_key)
    if not agent:
        raise HTTPException(status_code=404, detail="セッションが見つかりません")
    return agent


# ─────────────────────────────────────────────────
# エンドポイント
# ─────────────────────────────────────────────────

@router.post("/sessions", response_model=SessionResponse, summary="セッション開始")
async def start_session(
    req: StartSessionRequest,
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """
    エージェントセッションを開始し、最初の質問を返す。

    処理フロー:
    1. HanteiAgent を初期化
    2. search_candidates() で FAISS or initial_candidates から候補取得
    3. required - known = missing で最初の不明属性を導出
    4. Haiku で質問文を生成して返す
    5. セッション状態を DB に保存
    """
    if req.domain != "hantei":
        raise HTTPException(status_code=400, detail=f"domain='{req.domain}' は未実装です")

    session_id = str(uuid.uuid4())

    agent = create_hantei_agent(
        session_id=session_id,
        transaction_id=req.transaction_id,
        initial_query=req.initial_query,
        initial_candidates=req.initial_candidates,
        api_key=req.api_key,
    )

    # ツール登録
    for tool in default_tools():
        agent.register_tool(tool)

    # transaction_id がある場合は GetTransactionTool で自動補完
    if req.transaction_id:
        from platform_core.agent.tools import GetTransactionTool
        get_tx_tool = GetTransactionTool()
        try:
            await get_tx_tool.execute(agent.context)
        except Exception:
            pass

    resp = await agent.start_session_with_history(req.initial_query)

    # DB に保存
    await save_session(db, session_id, agent, domain=req.domain)

    return _agent_response_to_out(session_id, resp, _get_turn(agent))


@router.get("/sessions", summary="アクティブセッション一覧")
async def list_sessions(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """DB上のアクティブセッション一覧を返す"""
    sessions = await list_active_sessions(db)
    return {
        "count": len(sessions),
        "sessions": sessions,
    }


@router.get("/sessions/{session_id}", response_model=SessionResponse, summary="セッション状態取得")
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """現在のセッション状態を返す（候補・既知属性・次の質問）"""
    agent = await _load_or_404(db, session_id)

    # 現在の状態を再計算して返す（状態変更なし）
    resp = await agent.next_turn_async(user_input=None)
    return _agent_response_to_out(session_id, resp, _get_turn(agent))


@router.post(
    "/sessions/{session_id}/answer",
    response_model=SessionResponse,
    summary="ユーザー回答を送信",
)
async def submit_answer(
    session_id: str,
    req: AnswerRequest,
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """
    ユーザーの回答を受け取り、Context を更新して次の質問を返す。

    処理フロー:
    1. DB からセッション復元
    2. 前回の質問に対する回答で context.update_attribute()
    3. ontology.apply_rules() で候補を再絞り込み
    4. required - known = missing で次の不明属性を導出
    5. Haiku で次の質問文を生成、または is_ready_for_judgment=True を返す
    6. 更新状態を DB に保存
    """
    agent = await _load_or_404(db, session_id)

    resp = await agent.next_turn_with_history(user_input=req.answer)

    # DB に更新保存
    await save_session(db, session_id, agent)

    return _agent_response_to_out(session_id, resp, _get_turn(agent))


@router.post(
    "/sessions/{session_id}/judge",
    response_model=JudgmentResponse,
    summary="最終判定を実行",
)
async def execute_judgment(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> JudgmentResponse:
    """
    is_ready_for_judgment=True になった後に呼び出す。
    OntologyReasoningEngine + Sonnet で最終判定レポートを生成する。
    """
    agent = await _load_or_404(db, session_id)

    result = await agent.finalize()

    # 判定完了としてステータスを更新
    await mark_judged(db, session_id)

    return JudgmentResponse(
        session_id=session_id,
        overall_status=result.get("overall_status", "pending"),
        controlled_items=result.get("controlled_items", []),
        excluded_items=result.get("excluded_items", []),
        pending_items=result.get("pending_items", []),
        reasons=result.get("reasons", []),
        summary=result.get("summary", ""),
        judged_at=str(result.get("judged_at", "")),
    )


@router.delete("/sessions/{session_id}", summary="セッション破棄")
async def delete_session_endpoint(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """セッションを論理削除（status → "abandoned"）する"""
    deleted = await delete_session(db, session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="セッションが見つかりません")
    return {"deleted": True, "session_id": session_id}
