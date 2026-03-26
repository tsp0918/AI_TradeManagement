"""
platform_core.agent.session_repository
========================================
エージェントセッションの DB 永続化層。

AgentSessionORM (plat_agent_session) へのアクセスを集約し、
HanteiAgent の保存・復元・削除を担う。

設計方針:
    - context_snapshot (JSON) に TransactionContext 全体を格納
    - candidate_domain_ids (Text, カンマ区切り) に現在候補を格納
    - _last_missing_attr は dialogue_history[-1] から無損失に復元可能
    - api_key は DB に保存しない（常に環境変数から取得）
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.ontology.db.schema import AgentSessionORM
from platform_core.ontology.models.transaction import TransactionContext

if TYPE_CHECKING:
    from platform_core.agent.hantei_agent import HanteiAgent


# ─────────────────────────────────────────────────
# 保存
# ─────────────────────────────────────────────────

async def save_session(
    db: AsyncSession,
    session_id: str,
    agent: "HanteiAgent",
    domain: str = "hantei",
) -> None:
    """
    エージェント状態を plat_agent_session に upsert する。

    呼び出しタイミング:
        - セッション開始直後 (start_session)
        - 回答送信後 (submit_answer)
        - 判定実行後 (execute_judgment)
    """
    ctx_dict = agent.context.to_dict()
    candidates = agent.current_candidates  # list[str]
    candidates_str = ",".join(candidates)
    turn_count = len(ctx_dict.get("dialogue_history", []))
    tx_id = ctx_dict.get("transaction_id")

    # ステータス決定
    # 候補が 0（全除外）または 1 件以下 → 判定フェーズへ移行可能
    status = "active"
    if len(candidates) <= 1:
        status = "ready_for_judgment"

    result = await db.execute(
        select(AgentSessionORM).where(AgentSessionORM.session_id == session_id)
    )
    row = result.scalar_one_or_none()

    if row:
        row.context_snapshot = ctx_dict
        row.candidate_domain_ids = candidates_str
        row.status = status
        row.turn_count = turn_count
    else:
        db.add(AgentSessionORM(
            session_id=session_id,
            domain=domain,
            transaction_id=tx_id,
            context_snapshot=ctx_dict,
            candidate_domain_ids=candidates_str,
            status=status,
            turn_count=turn_count,
        ))

    await db.flush()


async def mark_judged(db: AsyncSession, session_id: str) -> None:
    """最終判定完了後にステータスを "judged" へ更新する"""
    result = await db.execute(
        select(AgentSessionORM).where(AgentSessionORM.session_id == session_id)
    )
    row = result.scalar_one_or_none()
    if row:
        row.status = "judged"
        await db.flush()


# ─────────────────────────────────────────────────
# 復元
# ─────────────────────────────────────────────────

async def load_session(
    db: AsyncSession,
    session_id: str,
    api_key: Optional[str] = None,
) -> Optional["HanteiAgent"]:
    """
    DB から HanteiAgent を復元する。
    セッションが存在しない、または abandoned の場合は None を返す。
    """
    result = await db.execute(
        select(AgentSessionORM).where(
            AgentSessionORM.session_id == session_id,
            AgentSessionORM.status != "abandoned",
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        return None

    return _restore_agent(row, api_key=api_key)


def _restore_agent(
    row: AgentSessionORM,
    api_key: Optional[str] = None,
) -> "HanteiAgent":
    """
    AgentSessionORM レコードから HanteiAgent を再構築する。

    復元ステップ:
        1. context_snapshot → TransactionContext (Pydantic) → HanteiContext
        2. candidate_domain_ids → _current_candidates
        3. dialogue_history[-1].attr_key (answer=None) → _last_missing_attr
        4. load_seed_ontology → OntologyReasoningEngine（シード固定）
        5. ClaudeLLMBridge (api_key は env var フォールバック)
    """
    from platform_core.agent.hantei_agent import HanteiAgent, HanteiContext
    from platform_core.ontology.db.repository import load_seed_ontology
    from platform_core.ontology.rules.engine import OntologyReasoningEngine
    from platform_core.llm_gateway.client import ClaudeLLMBridge

    # 1. TransactionContext 復元
    ctx_dict = row.context_snapshot or {}
    try:
        tx_ctx = TransactionContext(**ctx_dict)
    except Exception:
        # スキーマ変更等で復元不能な場合は空コンテキストで代替
        tx_ctx = TransactionContext(
            session_id=row.session_id,
            transaction_id=row.transaction_id,
        )
    context = HanteiContext(tx_ctx)

    # 2. 現在候補リスト復元（Text カラムが権威、context_snapshot も同期済みのはず）
    if row.candidate_domain_ids:
        current_candidates = [
            d for d in row.candidate_domain_ids.split(",") if d
        ]
    else:
        current_candidates = list(tx_ctx.candidate_domain_ids)

    # 3. エンジン・LLM ブリッジ生成
    kubanbang_list = load_seed_ontology()
    engine = OntologyReasoningEngine(kubanbang_list)
    llm_bridge = ClaudeLLMBridge(api_key=api_key)

    # 4. HanteiAgent 生成（initial_candidates に current を渡して再検索不要にする）
    agent = HanteiAgent(
        context=context,
        ontology=engine,
        llm_bridge=llm_bridge,
        initial_candidates=current_candidates,
    )
    agent._current_candidates = list(current_candidates)

    # 5. _last_missing_attr を対話履歴の末尾から復元
    history = tx_ctx.dialogue_history
    if history:
        last_turn = history[-1]
        if last_turn.answer is None and last_turn.attr_key:
            ma = engine.get_attribute_context(last_turn.attr_key)
            if ma:
                agent._last_missing_attr = ma

    return agent


# ─────────────────────────────────────────────────
# 削除
# ─────────────────────────────────────────────────

async def delete_session(db: AsyncSession, session_id: str) -> bool:
    """セッションを論理削除 (status → "abandoned") する"""
    result = await db.execute(
        select(AgentSessionORM).where(AgentSessionORM.session_id == session_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        return False
    row.status = "abandoned"
    await db.flush()
    return True


# ─────────────────────────────────────────────────
# 一覧
# ─────────────────────────────────────────────────

async def list_active_sessions(db: AsyncSession) -> list[dict]:
    """アクティブセッション一覧を返す（監査・管理用）"""
    result = await db.execute(
        select(
            AgentSessionORM.session_id,
            AgentSessionORM.domain,
            AgentSessionORM.transaction_id,
            AgentSessionORM.status,
            AgentSessionORM.turn_count,
            AgentSessionORM.created_at,
            AgentSessionORM.updated_at,
        ).where(
            AgentSessionORM.status.in_(["active", "ready_for_judgment"])
        ).order_by(AgentSessionORM.updated_at.desc())
    )
    rows = result.all()
    return [
        {
            "session_id": r.session_id,
            "domain": r.domain,
            "transaction_id": r.transaction_id,
            "status": r.status,
            "turn_count": r.turn_count,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]
