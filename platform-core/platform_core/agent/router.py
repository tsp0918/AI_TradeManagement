"""
platform_core.agent.router
============================
Platform Orchestrator Agent の FastAPI エンドポイント。

エンドポイント:
    POST /agent/sessions                     セッション開始（最初の質問を返す）
    GET  /agent/sessions/{session_id}        セッション状態取得
    POST /agent/sessions/{session_id}/answer ユーザー回答を送信（次の質問を返す）
    POST /agent/sessions/{session_id}/judge  最終判定を実行
    DELETE /agent/sessions/{session_id}      セッション破棄

セッション管理:
    Phase 2 ではインメモリ辞書を使用する（プロセスローカル）。
    Phase 3 で AgentSessionORM（plat_agent_session テーブル）への永続化に移行予定。
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from platform_core.agent.hantei_agent import HanteiAgent, create_hantei_agent
from platform_core.agent.tools import default_tools

router = APIRouter(prefix="/agent", tags=["agent"])

# ── インメモリセッションストア（Phase 2） ────────────────────────────────────
# key: session_id → HanteiAgent
_SESSION_STORE: dict[str, HanteiAgent] = {}


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


# ─────────────────────────────────────────────────
# エンドポイント
# ─────────────────────────────────────────────────

@router.post("/sessions", response_model=SessionResponse, summary="セッション開始")
async def start_session(req: StartSessionRequest) -> SessionResponse:
    """
    エージェントセッションを開始し、最初の質問を返す。

    処理フロー:
    1. HanteiAgent を初期化
    2. search_candidates() で FAISS or initial_candidates から候補取得
    3. required - known = missing で最初の不明属性を導出
    4. Haiku で質問文を生成して返す
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

    # セッションを保存
    _SESSION_STORE[session_id] = agent

    return _agent_response_to_out(session_id, resp, _get_turn(agent))


@router.get("/sessions/{session_id}", response_model=SessionResponse, summary="セッション状態取得")
async def get_session(session_id: str) -> SessionResponse:
    """現在のセッション状態を返す（候補・既知属性・次の質問）"""
    agent = _SESSION_STORE.get(session_id)
    if not agent:
        raise HTTPException(status_code=404, detail="セッションが見つかりません")

    # 現在の状態を再計算して返す
    resp = await agent.next_turn_async(user_input=None)
    return _agent_response_to_out(session_id, resp, _get_turn(agent))


@router.post(
    "/sessions/{session_id}/answer",
    response_model=SessionResponse,
    summary="ユーザー回答を送信",
)
async def submit_answer(session_id: str, req: AnswerRequest) -> SessionResponse:
    """
    ユーザーの回答を受け取り、Context を更新して次の質問を返す。

    処理フロー:
    1. 前回の質問に対する回答で context.update_attribute()
    2. ontology.apply_rules() で候補を再絞り込み
    3. required - known = missing で次の不明属性を導出
    4. Haiku で次の質問文を生成、または is_ready_for_judgment=True を返す
    """
    agent = _SESSION_STORE.get(session_id)
    if not agent:
        raise HTTPException(status_code=404, detail="セッションが見つかりません")

    resp = await agent.next_turn_with_history(user_input=req.answer)
    return _agent_response_to_out(session_id, resp, _get_turn(agent))


@router.post(
    "/sessions/{session_id}/judge",
    response_model=JudgmentResponse,
    summary="最終判定を実行",
)
async def execute_judgment(session_id: str) -> JudgmentResponse:
    """
    is_ready_for_judgment=True になった後に呼び出す。
    OntologyReasoningEngine + Sonnet で最終判定レポートを生成する。
    """
    agent = _SESSION_STORE.get(session_id)
    if not agent:
        raise HTTPException(status_code=404, detail="セッションが見つかりません")

    result = await agent.finalize()

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
async def delete_session(session_id: str) -> dict:
    """セッションをインメモリストアから削除する"""
    if session_id in _SESSION_STORE:
        del _SESSION_STORE[session_id]
        return {"deleted": True, "session_id": session_id}
    raise HTTPException(status_code=404, detail="セッションが見つかりません")


@router.get("/sessions", summary="アクティブセッション一覧")
async def list_sessions() -> dict:
    """現在メモリ上に存在するセッションの一覧を返す"""
    return {
        "count": len(_SESSION_STORE),
        "session_ids": list(_SESSION_STORE.keys()),
    }
