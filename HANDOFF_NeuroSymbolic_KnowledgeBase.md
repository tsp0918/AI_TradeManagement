# 引き継ぎドキュメント：NeuroSymbolic ナレッジベース設計
## AI_TradeManagement × DAP 共有知識基盤

**作成日：2026-03-17**  
**引き継ぎ先：Claude Code（VS Code）**  
**プロジェクト：AI_TradeManagement**

---

## 0. このドキュメントの目的

チャット上での検討内容（FAISS基礎 → グラフ理論・オントロジー → NeuroSymbolic AI → フェーズ1実装 → 共通フレームワーク設計）を Claude Code に引き継ぎ、**AI取引審査モジュールとDAPモジュールの両方で使える共有ナレッジベース**の実装を継続するための設計書。

### 設計状態サマリ

| コンポーネント | 設計 | 実装 |
|---|---|---|
| `shared_knowledge/ontology/models/` （4ファイル） | ✅ | ⬜ |
| `shared_knowledge/ontology/db/schema.py` | ✅ | ⬜ |
| `shared_knowledge/ontology/db/repository.py` | ✅ | ⬜ |
| `shared_knowledge/ontology/rules/engine.py` | ✅ | ⬜ |
| `shared_knowledge/ontology/agent/question_strategy.py` | ✅ | ⬜ |
| `shared_knowledge/ontology/seed/hantei_kubanbang.json` | ✅ サンプル有 | ⬜ 拡充必要 |
| Alembicマイグレーション | ✅ | ⬜ 未実行 |
| `shared_knowledge/agent/base_agent.py` | ✅ | ⬜ |
| `shared_knowledge/ontology/models/dap.py` | ✅ | ⬜ |
| `shared_knowledge/vector_store/faiss_bridge.py` | ✅ | ⬜ |
| `shared_knowledge/api/endpoints.py` | ✅ 骨格のみ | ⬜ |
| `shared_knowledge/agent/hantei_agent.py` | ⬜ 未設計 | ⬜ |
| `shared_knowledge/agent/dap_agent.py` | ⬜ 未設計 | ⬜ |

---

## 1. プロジェクト前提

```
AI_TradeManagement/
├── 該非判定モジュール（AI分類・FAISS・Claude API）
├── DAP-coachモジュール（Chrome拡張 + FastAPI）
├── pipeline_all_in_one.ipynb（Colab統合パイプライン）
└── shared_knowledge/  ← 新設。両モジュールが参照する共有知識基盤
```

**既存スタック**：FastAPI + SQLAlchemy 2.x + Alembic + PostgreSQL + FAISS  
**AI**：Claude Haiku（対話・バッチ）/ Claude Sonnet（法的解釈・最終判定）  
**開発環境**：VS Code + Claude Code CLI（ターミナル起動推奨） / Google Colab（GPU）

---

## 2. 設計の核心：今回の最重要洞察

> **オントロジー ＝「何を知らないかを知っている」構造**
>
> エージェントが賢いのは知識量が多いからではなく、  
> 「何が判定に必要で、今何が不明か」を**構造的に把握している**からである。  
> これはFAISSの「何に似ているか」だけでは絶対に実現できない、オントロジー固有の価値。

### NeuroSymbolicの役割分担

```
Neural（統計的）                     Symbolic（記号的）
FAISS / Embedding / LLM       +      オントロジー / ルール / 推論エンジン
──────────────────────────────────────────────────────────────────
「意味的に近いものを探す」            「論理的に正しいかを検証する」
曖昧な自然言語に対応                  説明・根拠・監査証跡を生成
System 1（直感・高速）                System 2（論理・低速）
```

---

## 3. ★ 革新的設計パターン：「不明属性の構造的導出」

本プロジェクト全体の核心。**`required - known = missing`** という差分計算が質問戦略を駆動する。

### 3-1. コードによる表現

```python
class OntologyReasoningEngine:

    def get_missing_attributes(self, transaction: Transaction) -> list:
        """
        ★ 「何を聞くべきか」をLLMが確率的に判断するのではなく、
          オントロジーの構造から論理的・決定論的に導出する。
        """
        # Step 1: FAISSで候補判定項番を取得（Neural部分）
        candidate_rules = self.faiss.search(transaction.product_description)

        # Step 2: 候補が「判定に必要とするパラメータ」をオントロジーから収集
        required_params = set()
        for rule in candidate_rules:
            required_params |= self.ontology.get(
                subject=rule,
                predicate="requires_parameter_for_judgment"
            )

        # Step 3: 差分が「聞くべきこと」← ここが核心の一行
        known   = set(transaction.known_attributes.keys())
        missing = required_params - known

        return self.sort_by_decision_impact(missing, candidate_rules)

    def generate_question(self, missing_attr: str, context: dict) -> str:
        """
        ★ 役割分担：
          「何を聞くか」→ オントロジーが決定（Symbolic）
          「どう聞くか」→ Claude Haikuが自然言語化（Neural）
        """
        attr_context = self.ontology.get_attribute_context(missing_attr)
        prompt = f"""
        あなたは外為法の専門家として輸出担当者に情報を確認しています。
        確認が必要な情報：{attr_context.label}
        その理由：{attr_context.regulatory_reason}
        これまでの文脈：{context}
        担当者が答えやすい質問を1つ、自然な日本語で生成してください。
        """
        return claude_haiku.generate(prompt)
```

### 3-2. オントロジー駆動の対話フロー（具体例）

```
入力：「加速度センサーXX-300を韓国に輸出したい」
  ↓
【Neural】FAISS候補検索：[2-7項, 4-3項, 10の項]
  ↓
【Symbolic】オントロジー照合：
  2-7項 → requires_parameter: バイアス安定性, 最大角速度
  4-3項 → requires_parameter: 分解能, サンプリングレート
  ↓
【Symbolic】不明属性を優先度順に導出（required - known）：
  1位：バイアス安定性（2-7項の閾値に直結）
  2位：用途・組み込み先システム（軍民判定に直結）
  3位：最大角速度 / 4位：需要者の業種
  ↓
【Neural】Claude Haikuが質問生成：
「このセンサーのバイアス安定性の仕様値を教えてください（例：0.01°/h）」

回答：「0.03°/hです」
  ↓
【Symbolic】推論：0.03 > 0.005（閾値）→ 2-7項を除外
  残候補：[4-3項, 10の項] → 次の不明属性：用途確認
  ↓
【Neural】次の質問生成 → 回答 → 推論 → ... （ループ）
  ↓
全属性が揃う → FAISS精密検索 + 推論エンジン → Sonnetレポート生成
```

### 3-3. この設計の本質的価値

```
【通常のチャットボット】
  LLMが「次に何を聞くか」を確率的に判断
  → 聞き忘れ・不要な質問・根拠のない判断が起きる

【オントロジー駆動エージェント】
  ✅ 聞き忘れがない（構造的に網羅）
  ✅ 不要なことを聞かない（候補が絞れれば不要属性を除外）
  ✅ 「なぜその質問か」を説明できる（監査対応）
  ✅ 回答によって候補がリアルタイムで絞り込まれる
  ✅ LLMの確率的揺らぎに依存しない安定した戦略
```

---

## 4. ディレクトリ構成（目標）

```
shared_knowledge/
├── __init__.py
├── agent/
│   ├── base_agent.py          ✅ 設計済み（本書セクション5）
│   ├── hantei_agent.py        ⬜ 未設計（BaseAgent派生）
│   ├── dap_agent.py           ⬜ 未設計（BaseAgent派生）
│   └── llm_bridge.py          ⬜ 未設計（Haiku/Sonnet切替）
├── ontology/
│   ├── models/
│   │   ├── regulation.py      ✅ 設計済み（セクション7-1）
│   │   ├── cargo.py           ✅ 設計済み
│   │   ├── transaction.py     ✅ 設計済み
│   │   ├── judgment.py        ✅ 設計済み
│   │   └── dap.py             ✅ 設計済み（本書セクション6）
│   ├── db/
│   │   ├── schema.py          ✅ 設計済み（セクション7-2）
│   │   ├── repository.py      ✅ 設計済み
│   │   ├── dependencies.py    ✅ 設計済み
│   │   └── migrations/        ✅ Alembic設定済み
│   ├── rules/
│   │   └── engine.py          ✅ 設計済み（セクション7-3）
│   ├── agent/
│   │   └── question_strategy.py ✅ 設計済み（セクション7-4）
│   └── seed/
│       └── hantei_kubanbang.json ✅ サンプル有（拡充必要）
├── vector_store/
│   ├── faiss_bridge.py        ✅ 設計済み（本書セクション8）
│   └── indexes/
│       ├── layer_a.index      （Colabで生成）
│       ├── layer_b.index      （Colabで生成）
│       └── layer_dap.index    （Colabで生成・未構築）
└── api/
    └── endpoints.py           ✅ 骨格設計済み（セクション9）
```

---

## 5. 共通BaseAgentフレームワーク（設計済み）

`shared_knowledge/agent/base_agent.py` に実装する。

### 5-1. 共通データ構造

```python
@dataclass
class MissingAttr:
    """「次に聞くべき属性」の1件。オントロジーが導出し、LLMが質問文に変換する。"""
    attr_key:  str            # 属性の識別子（例: "bias_stability"）
    label:     str            # 表示名（例: "バイアス安定性"）
    reason:    str            # なぜ必要か（例: "判定項番2-7の閾値判定に必要"）
    priority:  int            # 優先度（高いほど先に聞く）
    unit:      Optional[str]  # 単位ヒント（例: "°/h"）
    example:   Optional[str]  # 回答例（例: "0.01"）
    metadata:  dict           # ドメイン固有の追加情報


@dataclass
class AgentResponse:
    """エージェントが1ターンで返すもの"""
    question:              Optional[str]   # None なら判定フェーズへ移行
    missing_attr:          Optional[MissingAttr]
    context_snapshot:      dict            # 現在のContext状態
    candidates_remaining:  list[str]       # 残存候補IDリスト
    is_ready_for_judgment: bool = False
```

### 5-2. 抽象基底クラス

```python
class BaseContext(ABC):
    """エージェントが蓄積する文脈の基底クラス（該非判定・DAP共通）"""
    session_id: str

    @abstractmethod
    def get_known_attributes(self) -> dict[str, Any]: ...
    # 現在判明している属性のdict（attr_key → value）

    @abstractmethod
    def update_attribute(self, attr_key: str, value: Any) -> None: ...
    # 回答を受け取りContextを更新

    @abstractmethod
    def is_complete(self) -> bool: ...
    # 判定・案内に必要な情報が揃ったか

    @abstractmethod
    def to_dict(self) -> dict: ...
    # スナップショット（ログ・API応答用）


class BaseOntology(ABC):
    """オントロジーの基底クラス"""

    @abstractmethod
    def get_required_attributes(self, candidate_ids: list[str]) -> set[str]: ...
    # ★ 核心：候補IDリストから「判定に必要なattr_keyの集合」を返す

    @abstractmethod
    def get_attribute_context(self, attr_key: str) -> MissingAttr: ...
    # attr_keyに対応するラベル・理由・単位などを返す

    @abstractmethod
    def apply_rules(self, context: BaseContext, candidates: list[str]) -> dict: ...
    # 推論ルールを適用し、候補を絞り込む
    # 戻り値: {"remaining": [...], "excluded": {...}, "flags": [...]}


class BaseLLMBridge(ABC):
    """Haiku / Sonnet 切り替えの抽象化"""

    @abstractmethod
    def generate_question(self, missing_attr: MissingAttr, context: dict) -> str: ...
    # MissingAttrを受け取り自然言語の質問文を生成（Haiku）

    @abstractmethod
    def generate_report(self, context: dict, result: dict) -> str: ...
    # 最終結果を自然言語レポートに変換（Sonnet）
```

### 5-3. BaseAgent（共通ロジックの実装）

```python
class BaseAgent(ABC):
    """
    NeuroSymbolicエージェントの共通基底クラス。
    「required - known = missing → 質問」パターンをここに実装。
    HanteiAgent / DAPAgent はこれを継承し、
    ドメイン固有のContext・Ontology・FAISSを注入する。
    """

    def __init__(self, context: BaseContext, ontology: BaseOntology,
                 llm_bridge: BaseLLMBridge):
        self.context    = context
        self.ontology   = ontology
        self.llm_bridge = llm_bridge
        self._current_candidates: list[str] = []
        self._last_missing_attr: Optional[MissingAttr] = None

    # ── 派生クラスが実装するもの ──────────────────────

    @abstractmethod
    def search_candidates(self, query: str) -> list[str]:
        """FAISSで候補IDリストを取得（Neural部分）
        HanteiAgent → 判定項番ID
        DAPAgent    → プロセスID
        """

    @abstractmethod
    def finalize(self) -> dict:
        """全属性が揃った後の最終処理
        HanteiAgent → 推論エンジン実行 + Sonnetレポート
        DAPAgent    → 案内ステップ生成 + Sonnetレポート
        """

    # ── 共通ロジック（派生クラスが使い回す）──────────

    def start_session(self, initial_query: str) -> AgentResponse:
        """セッション開始：FAISSで候補を検索し、最初の質問を返す"""
        self._current_candidates = self.search_candidates(initial_query)
        self._last_missing_attr  = None
        return self.next_turn(user_input=None)

    def next_turn(self, user_input: Optional[str] = None) -> AgentResponse:
        """
        エージェントの1ターン処理。FastAPIから呼ばれる主要メソッド。
        """
        # Step 1: 回答でContextを更新 → オントロジー推論で候補を再絞り込み
        if user_input and self._last_missing_attr:
            self.context.update_attribute(
                self._last_missing_attr.attr_key, user_input
            )
            rule_result = self.ontology.apply_rules(
                self.context, self._current_candidates
            )
            self._current_candidates = rule_result["remaining"]

        # Step 2: 全情報揃ったか確認
        if self.context.is_complete() or not self._current_candidates:
            return AgentResponse(
                question=None, missing_attr=None,
                context_snapshot=self.context.to_dict(),
                candidates_remaining=self._current_candidates,
                is_ready_for_judgment=True,
            )

        # Step 3: ★ 核心：不明属性を構造的に導出
        required     = self.ontology.get_required_attributes(self._current_candidates)
        known        = set(self.context.get_known_attributes().keys())
        missing_keys = required - known  # ← required - known = missing

        if not missing_keys:
            return AgentResponse(
                question=None, missing_attr=None,
                context_snapshot=self.context.to_dict(),
                candidates_remaining=self._current_candidates,
                is_ready_for_judgment=True,
            )

        # Step 4: 優先度順にソートして最上位を選択
        missing_attrs = [self.ontology.get_attribute_context(k) for k in missing_keys]
        top_attr = sorted(missing_attrs, key=lambda a: -a.priority)[0]
        self._last_missing_attr = top_attr

        # Step 5: Claude Haikuで質問文を自然言語化（Neural部分）
        question = self.llm_bridge.generate_question(top_attr, self.context.to_dict())

        return AgentResponse(
            question=question,
            missing_attr=top_attr,
            context_snapshot=self.context.to_dict(),
            candidates_remaining=self._current_candidates,
            is_ready_for_judgment=False,
        )
```

### 5-4. 派生クラスのスタブ（実装が必要）

```python
# shared_knowledge/agent/hantei_agent.py  ← ⬜ 未設計

class HanteiAgent(BaseAgent):
    """該非判定エージェント"""

    def search_candidates(self, query: str) -> list[str]:
        # FAISSBridge.layer_a で判定項番IDを取得
        ...

    def finalize(self) -> dict:
        # OntologyReasoningEngine.reason() を実行
        # LLMBridge.generate_report() でSonnetレポート生成
        # JudgmentResultをDBに保存
        ...

    @classmethod
    def from_db(cls, session_id: str, db: Session) -> "HanteiAgent":
        # DBからTransactionContextを復元（セッション継続用）
        ...


# shared_knowledge/agent/dap_agent.py  ← ⬜ 未設計

class DAPAgent(BaseAgent):
    """DAPコーチエージェント"""

    def search_candidates(self, query: str) -> list[str]:
        # FAISSBridge.layer_dap でプロセスIDを取得
        ...

    def finalize(self) -> dict:
        # 案内ステップを組み立て
        # LLMBridge.generate_report() でSonnetレポート生成
        ...
```

---

## 6. DAPオントロジー設計（設計済み）

`shared_knowledge/ontology/models/dap.py` に実装する。

### 6-1. 該非判定との対応関係

```
該非判定                    DAP
────────────────────────────────────────────────────
HanteiKubanbang        ↔  GuidanceProcess
Threshold              ↔  Prerequisite
requires_parameter     ↔  requires_prerequisite  ← ★同じパターン
TransactionContext     ↔  UserSessionContext
EndUseType             ↔  UserIntentType
EndUserType            ↔  UserRoleType
JudgmentResult         ↔  GuidanceResult
```

### 6-2. Prerequisite（Thresholdに相当）

```python
class Prerequisite(BaseModel):
    """
    ガイドステップの前提条件。
    「この操作の案内には〇〇の確認が必要」を表現する。
    HanteiKubanbangのThresholdに相当。
    """
    condition_key:   str           # 属性キー（例: "user_role", "current_screen"）
    label:           str           # 表示名（例: "現在の画面"）
    expected_values: list[str]     # 空なら値の確認、非空ならこの値である必要がある
    description:     str = ""
    is_blocking:     bool = True   # Falseなら参考情報（判定には影響しない）
```

### 6-3. GuidanceProcess（HanteiKubanbangに相当）

```python
class GuidanceProcess(BaseModel):
    """
    業務プロセス・操作手順の1単位。
    HanteiKubanbangに相当するDAPオントロジーの核心クラス。

    ★ prerequisites が requires_parameter_for_judgment に相当。
      「この手順の案内に必要な前提条件確認リスト」が質問戦略の源泉になる。
    """
    process_id:   str
    platform:     SaaSPlatform   # salesforce / kintone / hubspot / generic
    title:        str
    description:  str

    # ★ Thresholdリストに相当：質問戦略の源泉
    prerequisites: list[Prerequisite] = []

    # 案内対象の意図・ロール
    applicable_intents: list[UserIntentType] = []
    applicable_roles:   list[UserRoleType]   = []

    # 操作手順・Tips
    steps: list[str] = []
    tips:  list[str] = []

    # グラフ構造（次のステップ・フォールバック）
    next_process_ids:     list[str] = []
    fallback_process_ids: list[str] = []

    # FAISS連携用
    embedding_text: Optional[str] = None

    def get_embedding_text(self) -> str:
        return f"{self.platform.value} {self.title} {self.description}"

    def get_required_attributes(self) -> list[str]:
        """質問戦略の源泉（HanteiKubanbang.get_required_parameters()に相当）"""
        return [p.condition_key for p in self.prerequisites]
```

### 6-4. UserSessionContext（TransactionContextに相当）

```python
class UserSessionContext(BaseModel):
    """DAPエージェントが蓄積するユーザー操作文脈"""
    session_id:     str
    initial_query:  str = ""
    platform:       Optional[SaaSPlatform] = None
    user_intent:    UserIntentType = UserIntentType.UNKNOWN
    user_role:      UserRoleType   = UserRoleType.UNKNOWN
    current_screen: Optional[str] = None
    current_record: Optional[str] = None

    # 汎用追加属性（Prerequisiteに対応）
    known_attributes: dict = {}

    # 推論結果
    candidate_process_ids: list[str] = []
    dialogue_history:      list[dict] = []

    def get_known_attributes(self) -> dict:
        base = {
            "platform":       self.platform.value if self.platform else None,
            "user_intent":    self.user_intent.value,
            "user_role":      self.user_role.value,
            "current_screen": self.current_screen,
        }
        return {k: v for k, v in {**base, **self.known_attributes}.items()
                if v is not None and v != "unknown"}

    def is_complete(self) -> bool:
        return (
            self.platform is not None and
            self.user_intent != UserIntentType.UNKNOWN and
            self.user_role   != UserRoleType.UNKNOWN
        )
```

---

## 7. 該非判定フェーズ1：設計済みコード概要

### 7-1. Pydanticモデル（`shared_knowledge/ontology/models/`）

| ファイル | 主要クラス |
|---|---|
| `regulation.py` | `HanteiKubanbang`, `Threshold`, `RegulationCategory`, `LicenseType`, `CountryGroup`, `ComparisonOperator` |
| `cargo.py` | `Cargo`, `CargoCategory`, `TechnicalParameter` |
| `transaction.py` | `TransactionContext`, `EndUseType`, `EndUserType`, `ConcernFlag` |
| `judgment.py` | `JudgmentResult`, `JudgmentReason`, `JudgmentStatus` |

`Threshold.is_controlled(actual_value)` が閾値判定の最小単位。

### 7-2. SQLAlchemyスキーマ（`shared_knowledge/ontology/db/schema.py`）

| テーブル | 役割 |
|---|---|
| `hantei_kubanbang` | 判定項番マスタ（`embedding_text`・`faiss_index_id`も保持） |
| `hantei_thresholds` | 閾値（1対多）← `requires_parameter_for_judgment`の物理的実体 |
| `concern_patterns` | 懸念キーワードパターン（`triggers_concern`の物理的実体） |
| `transaction_sessions` | エージェント対話セッション（対話ごとに更新） |
| `judgment_results` | 判定結果・監査ログ（`reasons`フィールドで根拠を完全記録） |

### 7-3. 推論エンジン（`shared_knowledge/ontology/rules/engine.py`）

`OntologyReasoningEngine.reason()` が全ルールを統合して `JudgmentResult` を返す。

- `apply_threshold_rules()` ← `Threshold.is_controlled()` を使用
- `apply_end_use_rules()` ← `concern_patterns` テーブルのキーワードで判定
- `apply_license_rules()` ← `license_requirements` JSON マッピングで決定
- `apply_catchall_rule()` ← 需要者 × 仕向地の組み合わせルール

### 7-4. 質問戦略エンジン（`shared_knowledge/ontology/agent/question_strategy.py`）

`QuestionStrategyEngine.get_next_question()` が：
1. TransactionContextの既知属性を確認
2. 候補判定項番が要求するパラメータとの差分計算（★核心）
3. 優先度順に次の質問を1つ返す
4. `None` を返したら全属性揃い → 判定フェーズへ

---

## 8. FAISSとオントロジーの接続点（設計済み）

`shared_knowledge/vector_store/faiss_bridge.py` に実装する。

### 8-1. 設計の要点

```
オントロジー（Symbolic）      FAISSBridge           FAISS Index
────────────────────────────────────────────────────────────────
候補IDリストを絞り込む  →  IDSelectorでフィルタ  →  絞り込み後の近傍検索
apply_rules()の結果    →  search_with_filter()  →  [(domain_id, distance)]
```

対話が進み Context が蓄積されるほど `allowed_domain_ids` が絞られ、検索精度が上がる。

### 8-2. FAISSBridgeの主要メソッド

```python
class FAISSBridge:
    def __init__(self, index_path: str, id_map: dict[int, str]):
        """
        id_map: FAISSの連番ID → ドメインID（判定項番番号・プロセスIDなど）
        """
        self.index   = faiss.read_index(index_path)
        self.id_map  = id_map
        self.rev_map = {v: k for k, v in id_map.items()}  # 逆引き

    def search(self, query_vector: np.ndarray, k: int = 20
               ) -> list[tuple[str, float]]:
        """フィルタなし検索（セッション開始時に候補Top20を取得）"""
        ...

    def search_with_filter(
        self,
        query_vector: np.ndarray,
        allowed_domain_ids: list[str],   # ← オントロジーが絞り込んだIDリスト
        k: int = 10,
    ) -> list[tuple[str, float]]:
        """
        オントロジーが絞り込んだIDリスト内でのみ検索する。
        ★ オントロジー → FAISSへのフィードバック接続点。
        """
        # IDSelectorを使ってFAISSインデックスをフィルタ検索
        allowed_faiss_ids = np.array(
            [self.rev_map[did] for did in allowed_domain_ids if did in self.rev_map],
            dtype=np.int64
        )
        selector = faiss.IDSelectorArray(allowed_faiss_ids)
        params   = faiss.SearchParameters()
        params.sel = selector
        distances, indices = self.index.search_with_params(
            query_vector.reshape(1, -1).astype("float32"), k, params
        )
        return [(self.id_map[int(idx)], float(dist))
                for dist, idx in zip(distances[0], indices[0]) if idx != -1]

    @classmethod
    def build(cls, embeddings: np.ndarray, domain_ids: list[str],
              output_path: str, index_type: str = "flat") -> "FAISSBridge":
        """
        Colabでembedding生成後にこのメソッドで.indexを構築・保存。
        CPU環境（FastAPI）でそのまま読み込める形で保存する。
        index_type: "flat"（精度優先・数万件以下）/ "ivf"（速度優先・数万件以上）
        """
        ...
```

### 8-3. Colabパイプラインへの組み込み

```python
# pipeline_all_in_one.ipynb に追加するセル

from shared_knowledge.vector_store.faiss_bridge import FAISSBridge

# 1. hantei_kubanbang.jsonからembedding_textを取得
texts      = [r["embedding_text"] for r in records]
domain_ids = [r["item_number"] for r in records]

# 2. embeddingモデルで変換（Colabで実行）
embeddings = model.encode(texts, normalize_embeddings=True)

# 3. Layer Aインデックスを構築・保存
bridge = FAISSBridge.build(
    embeddings=embeddings,
    domain_ids=domain_ids,
    output_path="data/staging/layer_a.index",
    index_type="flat",   # 2040件ならflatで十分
)

# 4. faiss_index_idをDBに書き戻す
for i, item_number in enumerate(domain_ids):
    db.execute(
        "UPDATE hantei_kubanbang SET faiss_index_id = :id WHERE item_number = :num",
        {"id": i, "num": item_number}
    )
```

---

## 9. FastAPIエンドポイント（骨格設計済み）

`shared_knowledge/api/endpoints.py` の骨格。`HanteiAgent` / `DAPAgent` の実装後に完成する。

```python
router = APIRouter()

@router.post("/sessions")
async def start_session(req: StartSessionRequest) -> StartSessionResponse:
    """
    セッション開始。domain="hantei" or "dap" でエージェントを切り替える。
    FAISSで候補を検索し、最初の質問を返す。
    """

@router.post("/sessions/{session_id}/answer")
async def submit_answer(session_id: str, req: AnswerRequest) -> AnswerResponse:
    """
    ユーザーの回答を受け取り、Contextを更新して次の質問を返す。
    毎回：Context更新 → オントロジー推論（候補再絞り込み）→ 次の不明属性導出 → Haiku質問生成
    """

@router.post("/sessions/{session_id}/judge")
async def execute_judgment(session_id: str) -> JudgmentResponse:
    """
    is_ready_for_judgment=True になってから呼ぶ。
    HanteiAgent: 推論エンジン + Sonnetレポート + DB保存
    DAPAgent:    案内ステップ生成 + Sonnetレポート + DB保存
    """
```

---

## 10. 全体アーキテクチャ（接続図）

```
【フロントエンド / Chrome拡張】
  POST /sessions → POST /sessions/{id}/answer × N → POST /sessions/{id}/judge
        ↓
┌──────────────────────────────────────────────────────────────┐
│  BaseAgent.next_turn()                                       │
│                                                              │
│  required = ontology.get_required_attributes(candidates)     │
│  known    = context.get_known_attributes()                   │
│  missing  = required - known           ← ★核心の一行        │
│                                                              │
│  top = sort_by_priority(missing)[0]                         │
│  question = llm_bridge.generate_question(top)  ← Haiku      │
└───────┬─────────────────────┬────────────────────────────────┘
        ↓                     ↓
┌───────▼──────────┐  ┌───────▼──────────────────────────────┐
│  BaseOntology    │  │  FAISSBridge                         │
│  （Symbolic）    │  │  search()           ← 初回・広く     │
│  apply_rules()   │◄─┤  search_with_filter() ← 絞り込み後  │
│  候補を再絞り込み │  │  ← オントロジーの候補リストを受け取る │
└───────┬──────────┘  └──────────────────────────────────────┘
        ↓
┌───────▼──────────────────────────────────────────────────────┐
│  finalize()                                                  │
│  HanteiAgent: OntologyReasoningEngine.reason()              │
│  DAPAgent:    案内ステップ組み立て                            │
│  共通: llm_bridge.generate_report()  ← Sonnet              │
│  共通: DB保存（judgment_results / dap_session_results）     │
└──────────────────────────────────────────────────────────────┘
```

---

## 11. 知識の3層構造

```
Layer 3：推論層（Symbolic）
  外為法オントロジー / DAPオントロジー
  ├─ クラス定義（何が存在するか）
  ├─ 関係定義（requires_parameter ← ★質問戦略の源泉）
  └─ ルール定義（IF-THEN 推論・候補絞り込み）

Layer 2：検索層（Neural）
  FAISSBridge
  ├─ layer_a（判定項番）
  ├─ layer_b（特許エビデンス）
  └─ layer_dap（DAPプロセス）← 未構築

Layer 1：対話層（Neural + Symbolic）
  BaseAgent
  ├─ Symbolic：オントロジーから「不明属性」を受け取る
  ├─ Neural：Claude Haikuが自然言語質問を生成
  └─ 回答をオントロジーに反映・文脈を蓄積
```

---

## 12. オントロジー実装ロードマップ

```
フェーズ1（今すぐ）：Pydantic + PostgreSQL
  設計済みコードを shared_knowledge/ に配置
  → Alembicマイグレーション実行
  → シードデータ投入
  → OntologyReasoningEngine を動かす
  → 最初のゴール：get_missing_attributes()が実際に動くこと

フェーズ2（検証後）：Neo4j 追加
  → 判定項番間の関係・法令継承をグラフで表現
  → FAISSとNeo4jを並行使用

フェーズ3（成熟後）：OWL推論器の統合
  → 矛盾検出・自動推論が必要になった段階で Owlready2 等に移行
```

---

## 13. 実装上の制約・注意事項

**Embeddingモデルの統一（最重要）**  
インデックス構築と検索クエリに必ず同一モデルを使う。モデルを変えた場合はインデックスを全再構築。

**IVF系インデックスはtrain()必須**
```python
index.train(train_vecs)   # 先にクラスタリング学習
index.add(vectors)        # その後にadd
```

**Colab→FastAPI連携時のGPU→CPU変換**
```python
cpu_index = faiss.index_gpu_to_cpu(gpu_index)
faiss.write_index(cpu_index, "layer_a.index")
```

**Claude Code拡張のクラッシュ回避**  
VSCode拡張がexit code 1でクラッシュする既知問題あり。ターミナルからCLI起動を推奨：
```bash
cd ~/Desktop/AI_TradeManagement && claude
```

---

## 14. Claude Codeへの最初の指示（推奨手順）

```
1. このドキュメントを精読する（特にセクション2・3の核心パターンを理解する）

2. 現在のAI_TradeManagementのディレクトリ構成を確認する

3. 既存のDBスキーマ・Alembic設定を確認し、競合がないかチェックする

4. shared_knowledge/ の配置場所を決定する
   → 該非判定・DAPの両方からどうimportするか

5. フェーズ1実装を開始する
   実装順：
   a. shared_knowledge/ontology/models/ （4ファイル + dap.py）
   b. shared_knowledge/agent/base_agent.py
   c. shared_knowledge/vector_store/faiss_bridge.py
   d. shared_knowledge/ontology/db/schema.py + migrations
   e. shared_knowledge/ontology/rules/engine.py
   f. shared_knowledge/agent/hantei_agent.py（⬜ 未設計：要設計）
   g. shared_knowledge/api/endpoints.py

6. 最初のマイルストーン：
   「加速度センサーXX-300を韓国に輸出したい」という入力に対して
   get_missing_attributes() が「バイアス安定性」を最優先で返すこと
```

---

*このドキュメントはチャット上での検討（FAISS基礎 → グラフ理論・オントロジー → NeuroSymbolic AI → フェーズ1設計 → BaseAgent・DAPオントロジー・FAISSBridge設計）の全内容を統合し、「`required - known = missing` がエージェントの質問戦略を駆動する」という核心設計パターンとともにClaude Codeに引き継ぐために作成された。*
