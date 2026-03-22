"""
platform_core.agent.base_agent
================================
NeuroSymbolic Platform Orchestrator Agent の抽象基底クラス群。

設計の核心:
    required = ontology.get_required_attributes(candidates)
    known    = context.get_known_attributes()
    missing  = required - known   ← この一行が質問戦略を決定する

役割分担:
    Symbolic (BaseOntology):  「何を確認すべきか」を構造的に導出
    Neural   (BaseLLMBridge): 「どう聞くか」を自然言語で生成
    Tools    (AgentTool):     「どのモジュールAPIを叩くか」を実行

HanteiAgent / DAPAgent / 将来のドメインエージェントは
BaseAgent を継承し、ドメイン固有の context・ontology・tools を注入する。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


# ─────────────────────────────────────────────────
# データ構造
# ─────────────────────────────────────────────────

@dataclass
class MissingAttr:
    """
    「次に確認すべき属性」の1件。
    オントロジーが構造的に導出し、LLM が質問文に変換する。
    """
    attr_key:  str            # 属性識別子  e.g. "operating_frequency_ghz"
    label:     str            # 表示名       e.g. "動作周波数"
    reason:    str            # なぜ必要か   e.g. "7項の閾値判定に必要"
    priority:  int            # 優先度（高いほど先）
    unit:      Optional[str]  # 単位ヒント   e.g. "GHz"
    example:   Optional[str]  # 回答例       e.g. "3.5"
    can_auto_retrieve: bool = False  # True: ツールで自動取得可
    auto_tool_name: Optional[str] = None  # 自動取得に使うツール名
    metadata:  dict = field(default_factory=dict)


@dataclass
class AgentResponse:
    """
    エージェントが 1 ターンで返す応答。
    question が None の場合は判定フェーズへ移行する合図。
    """
    question:              Optional[str]       # ユーザーへの次の質問
    missing_attr:          Optional[MissingAttr]
    context_snapshot:      dict                # 現在の Context 状態
    candidates_remaining:  list[str]           # 残存候補 domain_id リスト
    is_ready_for_judgment: bool = False
    auto_tool_executed:    Optional[str] = None  # 自動実行したツール名（あれば）


# ─────────────────────────────────────────────────
# AgentTool（モジュール API ラッパー）
# ─────────────────────────────────────────────────

class AgentTool(ABC):
    """
    エージェントが呼び出せる「ツール」の基底クラス。
    各モジュールの API 呼び出しをツールとしてラップし、
    エージェントがモジュールをまたいで業務フローを実行できるようにする。

    ツール例:
        RunValidationTool  - ai_validation パイプライン実行
        ScreeningTool      - 取引先スクリーニング照会
        HSClassifyTool     - HS コード分類
        PatentSearchTool   - 特許検索
        AskUserTool        - ユーザーへの質問（デフォルトツール）
    """
    name: str          # ツール識別子
    description: str   # エージェントがツール選択時に参照する説明

    @abstractmethod
    async def execute(self, context: "BaseContext", **kwargs) -> Any:
        """ツールを実行し結果を返す"""

    @abstractmethod
    def get_result_attr_key(self) -> Optional[str]:
        """
        このツールの実行結果を Context に格納する際の attr_key。
        None の場合は Context への自動格納を行わない。
        """


# ─────────────────────────────────────────────────
# 抽象基底クラス
# ─────────────────────────────────────────────────

class BaseContext(ABC):
    """エージェントが蓄積する文脈の基底クラス（该非判定・DAP 共通）"""

    @property
    @abstractmethod
    def session_id(self) -> str: ...

    @abstractmethod
    def get_known_attributes(self) -> dict[str, Any]:
        """現在判明している属性の dict（attr_key → value）"""

    @abstractmethod
    def update_attribute(self, attr_key: str, value: Any) -> None:
        """回答を受け取り Context を更新"""

    @abstractmethod
    def is_complete(self) -> bool:
        """判定・案内に必要な情報が揃ったか"""

    @abstractmethod
    def to_dict(self) -> dict:
        """スナップショット（ログ・API 応答用）"""


class BaseOntology(ABC):
    """
    オントロジーの基底クラス。
    「何が判定に必要か」を候補 ID リストから構造的に返す。
    """

    @abstractmethod
    def get_required_attributes(self, candidate_ids: list[str]) -> set[str]:
        """
        ★ 核心: 候補 ID リストから「判定に必要な attr_key の集合」を返す。
        required の源泉。
        """

    @abstractmethod
    def get_attribute_context(self, attr_key: str) -> Optional[MissingAttr]:
        """attr_key に対応する MissingAttr（ラベル・理由・単位など）を返す"""

    @abstractmethod
    def apply_rules(
        self, context: BaseContext, candidates: list[str]
    ) -> dict[str, Any]:
        """
        推論ルールを適用し、候補を絞り込む。
        戻り値: {
            "remaining": [...],    # 残存候補 domain_id
            "excluded":  {...},    # 除外された domain_id とその理由
            "flags":     [...],    # 懸念フラグ
        }
        """


class BaseLLMBridge(ABC):
    """
    Haiku / Sonnet 切り替えの抽象化。
    Symbolic が「何を聞くか」を決定し、Neural がここで「どう聞くか」に変換する。
    """

    @abstractmethod
    async def generate_question(
        self, missing_attr: MissingAttr, context_dict: dict
    ) -> str:
        """MissingAttr を受け取り自然言語の質問文を生成（Haiku）"""

    @abstractmethod
    async def generate_report(
        self, context_dict: dict, result: dict
    ) -> str:
        """最終結果を自然言語レポートに変換（Sonnet）"""


# ─────────────────────────────────────────────────
# BaseAgent（共通ロジック実装）
# ─────────────────────────────────────────────────

class BaseAgent(ABC):
    """
    NeuroSymbolic Platform Orchestrator Agent の共通基底クラス。

    `required - known = missing → 質問 or ツール実行` パターンをここに実装。
    HanteiAgent / DAPAgent などの具象クラスは BaseAgent を継承し、
    ドメイン固有の Context・Ontology・Tools・LLMBridge を注入する。

    MCP 的設計:
        各モジュール API はツール (AgentTool) としてレジストリに登録される。
        エージェントは「自動取得できる属性」はツールを呼び、
        「ユーザーに聞くしかない属性」は質問に変換する。
    """

    def __init__(
        self,
        context:    BaseContext,
        ontology:   BaseOntology,
        llm_bridge: BaseLLMBridge,
        tools:      Optional[list[AgentTool]] = None,
    ):
        self.context    = context
        self.ontology   = ontology
        self.llm_bridge = llm_bridge
        self._tools: dict[str, AgentTool] = {t.name: t for t in (tools or [])}
        self._current_candidates: list[str] = []
        self._last_missing_attr: Optional[MissingAttr] = None

    # ── 派生クラスが必ず実装するもの ──────────────────────────────────────

    @abstractmethod
    def search_candidates(self, query: str) -> list[str]:
        """
        FAISS で候補 domain_id リストを取得（Neural 部分）。
        HanteiAgent → 判定項番 domain_id
        DAPAgent    → プロセス domain_id
        """

    @abstractmethod
    async def finalize(self) -> dict:
        """
        全属性が揃った後の最終処理。
        HanteiAgent → 推論エンジン実行 + Sonnet レポート + DB 保存
        DAPAgent    → 案内ステップ生成 + Sonnet レポート
        """

    # ── 共通ロジック ───────────────────────────────────────────────────────

    async def start_session_async(self, initial_query: str) -> AgentResponse:
        """セッション開始: 候補を検索し、最初の質問を返す"""
        self._current_candidates = self.search_candidates(initial_query)
        self._last_missing_attr  = None
        return await self.next_turn_async(user_input=None)

    async def next_turn_async(
        self, user_input: Optional[str] = None
    ) -> AgentResponse:
        """
        エージェントの 1 ターン処理（非同期版）。FastAPI から呼ばれる主要メソッド。

        Step 1: 回答で Context を更新 → オントロジー推論で候補を再絞り込み
        Step 2: 全情報揃ったか確認
        Step 3: ★ 核心: required - known = missing で不明属性を導出
        Step 4: 自動取得できる属性はツールで取得（MCP 的振る舞い）
        Step 5: 残りはHaikuで質問文に変換
        """
        auto_tool_name: Optional[str] = None

        # Step 1: ユーザー回答を Context に反映 → オントロジー推論
        if user_input is not None and self._last_missing_attr:
            self.context.update_attribute(
                self._last_missing_attr.attr_key, user_input
            )
            rule_result = self.ontology.apply_rules(
                self.context, self._current_candidates
            )
            self._current_candidates = rule_result.get("remaining", self._current_candidates)

        # Step 2: 完了チェック
        if self.context.is_complete() or not self._current_candidates:
            return AgentResponse(
                question=None,
                missing_attr=None,
                context_snapshot=self.context.to_dict(),
                candidates_remaining=self._current_candidates,
                is_ready_for_judgment=True,
            )

        # Step 3: ★ required - known = missing
        required     = self.ontology.get_required_attributes(self._current_candidates)
        known        = set(self.context.get_known_attributes().keys())
        missing_keys = required - known

        if not missing_keys:
            return AgentResponse(
                question=None,
                missing_attr=None,
                context_snapshot=self.context.to_dict(),
                candidates_remaining=self._current_candidates,
                is_ready_for_judgment=True,
            )

        # Step 4: 優先度順にソートし、自動取得できる属性はツールで実行
        missing_attrs = [
            ma for ma in (self.ontology.get_attribute_context(k) for k in missing_keys)
            if ma is not None
        ]
        missing_attrs.sort(key=lambda a: -a.priority)

        for ma in missing_attrs:
            if ma.can_auto_retrieve and ma.auto_tool_name in self._tools:
                tool = self._tools[ma.auto_tool_name]
                try:
                    result_value = await tool.execute(self.context)
                    if tool.get_result_attr_key():
                        self.context.update_attribute(tool.get_result_attr_key(), result_value)
                    auto_tool_name = ma.auto_tool_name
                    # 自動取得できたので missing から除外 → 再帰的に次を探す
                    return await self.next_turn_async(user_input=None)
                except Exception:
                    pass  # 自動取得失敗時はユーザーに質問にフォールバック

        # Step 5: 最優先の未知属性を Haiku で質問文に変換
        top_attr = missing_attrs[0]
        self._last_missing_attr = top_attr
        question = await self.llm_bridge.generate_question(
            top_attr, self.context.to_dict()
        )

        return AgentResponse(
            question=question,
            missing_attr=top_attr,
            context_snapshot=self.context.to_dict(),
            candidates_remaining=self._current_candidates,
            is_ready_for_judgment=False,
            auto_tool_executed=auto_tool_name,
        )

    def register_tool(self, tool: AgentTool) -> None:
        """ツールを動的に登録する"""
        self._tools[tool.name] = tool

    @property
    def current_candidates(self) -> list[str]:
        return list(self._current_candidates)
