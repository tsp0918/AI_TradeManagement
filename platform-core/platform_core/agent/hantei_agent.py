"""
platform_core.agent.hantei_agent
===================================
該非判定エージェント。BaseAgent を継承し、外為法オントロジーと連動する。

設計:
    - search_candidates(): ai_validation API または FAISS 直接呼び出しで候補を取得
    - finalize():          OntologyReasoningEngine.reason() + LLM レポート生成
    - HanteiContext:       TransactionContext を BaseContext として公開するアダプタ

FAISS の利用戦略:
    platform_core が FAISS をロードしていない場合は、
    呼び出し元が initial_candidates を直接渡す形式をサポートする。
    ai_validation モジュールが FAISS を持つ場合はそちらを活用する（Phase 2 以降）。
"""
from __future__ import annotations

from typing import Any, Optional

from platform_core.agent.base_agent import (
    AgentResponse,
    BaseAgent,
    BaseContext,
    BaseLLMBridge,
    BaseOntology,
    AgentTool,
)
from platform_core.ontology.models.transaction import TransactionContext
from platform_core.ontology.rules.engine import OntologyReasoningEngine


# ─────────────────────────────────────────────────
# HanteiContext（TransactionContext → BaseContext アダプタ）
# ─────────────────────────────────────────────────

class HanteiContext(BaseContext):
    """
    TransactionContext を BaseContext インターフェースに適合させるアダプタ。
    """

    def __init__(self, tx_context: TransactionContext):
        self._ctx = tx_context

    @property
    def session_id(self) -> str:
        return self._ctx.session_id

    @property
    def transaction_id(self) -> Optional[int]:
        return self._ctx.transaction_id

    @property
    def candidate_domain_ids(self) -> list[str]:
        return self._ctx.candidate_domain_ids

    @candidate_domain_ids.setter
    def candidate_domain_ids(self, value: list[str]) -> None:
        self._ctx.candidate_domain_ids = value

    def get_known_attributes(self) -> dict[str, Any]:
        return self._ctx.get_known_attributes()

    def update_attribute(self, attr_key: str, value: Any) -> None:
        self._ctx.update_attribute(attr_key, value)
        # 対話履歴に記録
        if self._ctx.dialogue_history:
            last = self._ctx.dialogue_history[-1]
            if last.answer is None and last.attr_key == attr_key:
                last.answer = str(value)

    def is_complete(self) -> bool:
        return self._ctx.is_complete()

    def to_dict(self) -> dict:
        return self._ctx.to_dict()

    @property
    def raw(self) -> TransactionContext:
        """内部の TransactionContext に直接アクセスする"""
        return self._ctx

    def add_question_to_history(self, question: str, attr_key: Optional[str]) -> None:
        self._ctx.add_dialogue_turn(question=question, attr_key=attr_key)


# ─────────────────────────────────────────────────
# FAISS hit → domain_id マッピングユーティリティ
# ─────────────────────────────────────────────────

def hit_to_domain_id(hit: Any) -> str:
    """
    LayerAHit を domain_id 文字列に変換する。
    source_type="eccn" の場合は extra["eccn"] を使う。
    """
    source_type = getattr(hit, "source_type", "")
    if source_type == "eccn":
        eccn = (getattr(hit, "extra", {}) or {}).get("eccn", "")
        return f"ECCN::{eccn}" if eccn else ""

    item_no    = getattr(hit, "item_no", "")
    article_no = (getattr(hit, "extra", {}) or {}).get("article_no", "")
    return f"FEFTA::{item_no}::{source_type}::{article_no}"


def hits_to_domain_ids(hits: list[Any], registry_ids: set[str]) -> list[str]:
    """
    LayerAHit のリストを domain_id リストに変換し、
    オントロジー登録済みのもののみを返す（重複排除済み）。
    """
    seen: set[str] = set()
    result: list[str] = []
    for h in hits:
        did = hit_to_domain_id(h)
        if did and did in registry_ids and did not in seen:
            seen.add(did)
            result.append(did)
    return result


# ─────────────────────────────────────────────────
# HanteiAgent
# ─────────────────────────────────────────────────

class HanteiAgent(BaseAgent):
    """
    外為法・EAR 該非判定エージェント。

    search_candidates():
        FAISS Layer A で候補の規制項番 (domain_id) を取得する。
        FAISS が利用不可の場合は initial_candidates フォールバックを使う。

    finalize():
        OntologyReasoningEngine.reason() で判定を実行し、
        Sonnet でサマリーレポートを生成する。
    """

    def __init__(
        self,
        context:             HanteiContext,
        ontology:            OntologyReasoningEngine,
        llm_bridge:          BaseLLMBridge,
        tools:               Optional[list[AgentTool]] = None,
        initial_candidates:  Optional[list[str]]       = None,
    ):
        super().__init__(context, ontology, llm_bridge, tools)
        self._engine             = ontology          # 型付きで保持
        self._initial_candidates = initial_candidates or []

    def search_candidates(self, query: str) -> list[str]:
        """
        FAISS Layer A で候補 domain_id を検索する。

        1. platform_core/services/faiss_e5_service が利用可能であれば使う
        2. 利用不可の場合は initial_candidates フォールバック
        3. どちらもなければオントロジーの全 domain_id を返す（広く当てる）
        """
        # フォールバック優先（初期セッション起動時に外部から渡された候補）
        if self._initial_candidates:
            return list(self._initial_candidates)

        # FAISS 検索を試みる
        try:
            from platform_core.services.faiss_e5_service import (
                is_ready,
                search_layer_a,
            )
            if is_ready():
                hits = search_layer_a(query, top_k=15)
                registry = set(self._engine.all_domain_ids)
                candidates = hits_to_domain_ids(hits, registry)
                if candidates:
                    return candidates
        except Exception:
            pass

        # どちらも取れない場合はオントロジー全 domain_id（全候補から絞り込み開始）
        return list(self._engine.all_domain_ids)

    async def finalize(self) -> dict:
        """
        全属性情報が揃った後の最終判定処理。

        1. OntologyReasoningEngine.reason() で判定実行
        2. Sonnet でサマリーレポート生成
        3. JudgmentResult を dict で返す
        """
        result = self._engine.reason(
            self.context, self._current_candidates or self._engine.all_domain_ids
        )

        # Sonnet でサマリーレポートを生成
        summary = await self.llm_bridge.generate_report(
            self.context.to_dict(),
            result.to_dict(),
        )
        result.summary = summary

        return result.to_dict()

    async def next_turn_with_history(
        self, user_input: Optional[str] = None
    ) -> AgentResponse:
        """
        next_turn_async のラッパー。対話履歴への記録を追加する。
        """
        resp = await self.next_turn_async(user_input)

        # 質問を履歴に記録
        if resp.question and isinstance(self.context, HanteiContext):
            attr_key = resp.missing_attr.attr_key if resp.missing_attr else None
            self.context.add_question_to_history(resp.question, attr_key)

        # 候補を context に同期
        if isinstance(self.context, HanteiContext):
            self.context.candidate_domain_ids = resp.candidates_remaining

        return resp


# ─────────────────────────────────────────────────
# ファクトリ関数
# ─────────────────────────────────────────────────

def create_hantei_agent(
    session_id:          str,
    transaction_id:      Optional[int]       = None,
    initial_query:       str                 = "",
    initial_candidates:  Optional[list[str]] = None,
    api_key:             Optional[str]       = None,
) -> HanteiAgent:
    """
    HanteiAgent を標準構成で生成するファクトリ関数。

    Args:
        session_id:         セッション識別子
        transaction_id:     既存トランザクション ID（ai_validation との連携用）
        initial_query:      最初の自然言語クエリ
        initial_candidates: FAISS検索済み domain_id リスト（省略時は内部で検索）
        api_key:            Anthropic API キー（省略時は環境変数から取得）
    """
    from platform_core.ontology.db.repository import load_seed_ontology
    from platform_core.llm_gateway.client import ClaudeLLMBridge

    # オントロジーをシードデータからロード
    kubanbang_list = load_seed_ontology()
    engine = OntologyReasoningEngine(kubanbang_list)

    # コンテキスト初期化
    tx_ctx = TransactionContext(
        session_id=session_id,
        transaction_id=transaction_id,
        initial_query=initial_query,
    )
    context = HanteiContext(tx_ctx)

    # LLM ブリッジ
    llm_bridge = ClaudeLLMBridge(api_key=api_key)

    return HanteiAgent(
        context=context,
        ontology=engine,
        llm_bridge=llm_bridge,
        initial_candidates=initial_candidates,
    )
