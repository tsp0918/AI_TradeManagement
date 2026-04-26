# HANDOFF: DAP LLM Hybrid構成 移行設計書

**対象モジュール:** AI_TradeManagement / DAP (Digital Adoption Platform) コーチ  
**作成日:** 2026-03-25  
**ステータス:** 検証待ち → 実装待ち  

---

## 1. 背景と目的

### 現状
- DAPコーチのLLMパイプラインは全ステージをClaude API（Haiku / Sonnet）で構成
- 開発環境をIntel MacBook Air → **M4 Mac mini (32GB RAM)** に移管済み

### 移行の動機
| 目的 | 詳細 |
|---|---|
| コスト削減 | Stage 1/3の高頻度API呼び出しをローカル化 |
| レスポンス改善 | ローカル推論による初回応答レイテンシの短縮 → CX向上 |

### 移行方針
**フル移行ではなくHybrid構成を採用する。**  
外為法・EARの複雑推論（Stage 2）はClaude Sonnet APIを維持し、品質を守る。  
意図分類（Stage 1）とJSON生成（Stage 3）のみローカル化する。

---

## 2. 現構成（移行前）

```
Stage 1: 意図理解          → Claude Haiku API
Stage 2: プロセスマッピング  → Claude Sonnet API + Extended Thinking
Stage 3: アクション生成JSON  → Claude Haiku API
```

**問題点:**
- Stage 1/3は軽量タスクだがAPI呼び出しのオーバーヘッドが毎回発生
- 全ステージAPI依存のためコストがリニアにスケール

---

## 3. 目標構成（移行後）

### モデル選定

| Stage | モデル | 実行場所 | 理由 |
|---|---|---|---|
| Stage 1: 意図理解 | Qwen2.5-14B | Ollama (local) | 速度・日本語精度のバランス最良。32GBで余裕動作 |
| Stage 2: プロセスマッピング | Claude Sonnet API | Anthropic API | 外為法推論の品質保持。移行しない |
| Stage 3: JSON生成 | Qwen2.5-14B | Ollama (local) | Stage 1と同モデル共有。スキーマ固定で安定動作 |

> **Qwen2.5-32Bを選ばない理由:**  
> ロード時間・メモリ消費増でStage 1/3の速度メリットが薄れる。  
> 意図分類・JSON生成は「賢さ」より「速さと安定性」が優先される。  
> M4のNeural Engine活用で14Bは30〜50 tok/s程度が期待できる。

### パイプライン全体像

```
Chrome Extension
    ↓ DOM / ユーザー操作イベント
FastAPI Backend
    ↓
┌─────────────────────────────────┐
│  Stage 1: 意図理解              │
│  Qwen2.5-14B / Ollama (local)  │  ← 高頻度・低レイテンシ
│  インテント分類 + 信頼スコア出力  │
└────────────┬────────────────────┘
             │
    ┌────────┴──────────────┐
    │  信頼スコア ルーティング  │
    │  score >= 0.85?        │
    └────────┬──────────────┘
           Yes│                    No（複雑・曖昧）
              ↓                         ↓
 ┌─────────────────────┐   ┌────────────────────────────┐
 │  Stage 3            │   │  Stage 2                   │
 │  JSON生成           │   │  Claude Sonnet API         │
 │  Qwen2.5-14B (local)│   │  Extended Thinking         │
 │  スキーマ固定出力    │   │  プロセスマッピング         │
 └─────────────────────┘   └──────────┬─────────────────┘
                                       ↓
                            ┌─────────────────────┐
                            │  Stage 3             │
                            │  JSON生成            │
                            │  Claude Haiku API    │
                            └─────────────────────┘
```

**信頼スコアによる動的ルーティングがポイント。**  
明確な操作指示（「この取引を保存して」等）はローカル完結。  
外為法解釈・複雑なプロセス判断が必要なケースのみSonnet APIに流す。

---

## 4. 期待効果

| 指標 | 現状（全API） | 移行後（Hybrid） |
|---|---|---|
| Stage 1 レイテンシ | ~500ms + API往復 | ~200ms (local) |
| Stage 3 レイテンシ | ~500ms + API往復 | ~300ms (local) |
| API呼び出しコスト | 全ステージ課金 | Stage 2のみ課金 |
| 推定コスト削減率 | - | **50〜70%削減**（日常操作の7〜8割がStage 2不要と想定） |

---

## 5. 実装手順

### Step 1: Ollamaセットアップ

```bash
# Ollamaインストール
brew install ollama

# Qwen2.5-14Bモデル取得（約9GB）
ollama pull qwen2.5:14b

# 動作確認
ollama run qwen2.5:14b "日本語でこんにちは"

# バックグラウンドサービス起動確認
ollama serve
```

---

### Step 2: FastAPIにOllamaクライアント追加

**依存パッケージ追加:**
```bash
pip install ollama
```

**`dap/llm_router.py` を新規作成（または既存のLLMクライアントファイルに追記）:**

```python
import ollama
import json
import logging
from anthropic import Anthropic

logger = logging.getLogger(__name__)

anthropic_client = Anthropic()

# ==============================
# Stage 1: 意図理解（ローカル）
# ==============================

INTENT_SYSTEM_PROMPT = """
あなたはトレードコンプライアンスシステムのDAP（デジタルアドプションプラットフォーム）コーチです。
ユーザーの発話からインテントを分類し、以下のJSON形式のみで回答してください。

出力フォーマット（JSON以外は絶対に出力しない）:
{
  "intent": "<インテントラベル>",
  "confidence": <0.0〜1.0の信頼スコア>,
  "entities": {
    "action": "<検出されたアクション>",
    "target": "<対象オブジェクト>",
    "context": "<外為法/EAR等の規制文脈>"
  },
  "requires_complex_reasoning": <true/false>
}

インテントラベル一覧:
- SAVE_TRANSACTION: 取引の保存・登録
- CLASSIFY_ITEM: 品目の該非判定・分類
- CHECK_COMPLIANCE: コンプライアンス確認
- SEARCH_ECCN: ECCN/ECCNコード検索
- NAVIGATE_UI: 画面遷移・UI操作
- EXPLAIN_REGULATION: 規制説明・質問
- UNKNOWN: 判定不能
"""

async def classify_intent_local(user_message: str) -> dict:
    """Stage 1: ローカルLLMで意図分類"""
    try:
        response = ollama.chat(
            model='qwen2.5:14b',
            messages=[
                {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                {"role": "user", "content": user_message}
            ],
            format="json",
            options={"temperature": 0.1}  # 分類タスクは低temperatureで安定化
        )
        result = json.loads(response['message']['content'])
        logger.info(f"[Stage1/Local] intent={result.get('intent')}, confidence={result.get('confidence')}")
        return result
    except Exception as e:
        logger.error(f"[Stage1/Local] Error: {e}")
        # フォールバック: Haiku APIへ
        return await classify_intent_api_fallback(user_message)


async def classify_intent_api_fallback(user_message: str) -> dict:
    """Stage 1 フォールバック: Claude Haiku API"""
    response = anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        system=INTENT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}]
    )
    return json.loads(response.content[0].text)


# ==============================
# Stage 2: プロセスマッピング（API維持）
# ==============================

async def map_process_api(intent_result: dict, user_message: str, context: dict) -> dict:
    """Stage 2: Claude Sonnet APIでプロセスマッピング（複雑ケースのみ）"""
    response = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        thinking={"type": "enabled", "budget_tokens": 1024},
        system="外為法・EAR規制の専門家として、ユーザーの操作プロセスをステップ分解してください。",
        messages=[
            {
                "role": "user",
                "content": f"インテント: {json.dumps(intent_result, ensure_ascii=False)}\n"
                           f"ユーザー発話: {user_message}\n"
                           f"現在のコンテキスト: {json.dumps(context, ensure_ascii=False)}"
            }
        ]
    )
    return {"process_steps": response.content[-1].text}


# ==============================
# Stage 3: JSON生成（ローカル）
# ==============================

ACTION_SCHEMA = {
    "action_type": "string (CLICK|INPUT|NAVIGATE|HIGHLIGHT|TOOLTIP)",
    "target_selector": "string (CSSセレクタ)",
    "value": "string | null",
    "message": "string (ユーザーへの日本語ガイダンス)",
    "next_step": "string | null"
}

ACTION_SYSTEM_PROMPT = f"""
以下のJSONスキーマに厳密に従い、アクション指示をJSON配列で出力してください。
スキーマ: {json.dumps(ACTION_SCHEMA, ensure_ascii=False)}

- JSON配列のみを出力。説明文・マークダウン・コードブロック記法は禁止。
- messageは必ず丁寧な日本語。
- target_selectorはCSSセレクタ形式。
"""

async def generate_action_local(intent_result: dict, process_steps: str | None = None) -> list:
    """Stage 3: ローカルLLMでアクションJSON生成"""
    prompt = f"インテント情報: {json.dumps(intent_result, ensure_ascii=False)}"
    if process_steps:
        prompt += f"\nプロセスステップ: {process_steps}"

    try:
        response = ollama.chat(
            model='qwen2.5:14b',
            messages=[
                {"role": "system", "content": ACTION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            format="json",
            options={"temperature": 0.0}  # JSON生成はtemperature=0で確定的出力
        )
        raw = response['message']['content']
        # JSON配列またはオブジェクトを安全にパース
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else [parsed]
    except Exception as e:
        logger.error(f"[Stage3/Local] Error: {e}")
        return await generate_action_api_fallback(intent_result, process_steps)


async def generate_action_api_fallback(intent_result: dict, process_steps: str | None) -> list:
    """Stage 3 フォールバック: Claude Haiku API"""
    prompt = f"インテント情報: {json.dumps(intent_result, ensure_ascii=False)}"
    if process_steps:
        prompt += f"\nプロセスステップ: {process_steps}"

    response = anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        system=ACTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip().replace("```json", "").replace("```", "")
    return json.loads(raw)


# ==============================
# メインルーター
# ==============================

CONFIDENCE_THRESHOLD = 0.85

async def run_dap_pipeline(user_message: str, context: dict = {}) -> dict:
    """
    DAPパイプライン実行。
    信頼スコアに応じてStage 2をスキップし、ローカルで完結させる。
    """
    # Stage 1: 意図理解（ローカル）
    intent_result = await classify_intent_local(user_message)
    confidence = intent_result.get("confidence", 0.0)
    requires_complex = intent_result.get("requires_complex_reasoning", False)

    process_steps = None

    # Stage 2: 複雑ケースのみAPIへ
    if confidence < CONFIDENCE_THRESHOLD or requires_complex:
        logger.info(f"[Router] → Stage 2 (API): confidence={confidence}, complex={requires_complex}")
        process_map = await map_process_api(intent_result, user_message, context)
        process_steps = process_map.get("process_steps")
    else:
        logger.info(f"[Router] → Stage 2 スキップ: confidence={confidence}")

    # Stage 3: アクション生成（ローカル）
    actions = await generate_action_local(intent_result, process_steps)

    return {
        "intent": intent_result,
        "process_steps": process_steps,
        "actions": actions,
        "routed_to_api": process_steps is not None
    }
```

---

### Step 3: ABテスト用の精度計測スクリプト

**`dap/ab_test_intent.py` を新規作成:**

```python
"""
Stage 1 ABテスト: Haiku API vs Qwen2.5-14B Local
インテント分類の一致率を計測する
"""
import asyncio
import json
from dap.llm_router import classify_intent_local, classify_intent_api_fallback

# テストケース: DAPで想定される典型的な発話
TEST_CASES = [
    {"input": "この取引を保存してください", "expected_intent": "SAVE_TRANSACTION"},
    {"input": "ECCNコードを確認したい", "expected_intent": "SEARCH_ECCN"},
    {"input": "この品目は規制対象ですか？", "expected_intent": "CLASSIFY_ITEM"},
    {"input": "輸出許可が必要かどうか調べて", "expected_intent": "CHECK_COMPLIANCE"},
    {"input": "取引一覧ページに移動して", "expected_intent": "NAVIGATE_UI"},
    {"input": "外為法第25条の内容を教えて", "expected_intent": "EXPLAIN_REGULATION"},
    # 複雑ケース（Stage 2に流れるべきもの）
    {"input": "半導体製造装置をシンガポール法人経由で輸出する場合のリスクは？", "expected_intent": "CHECK_COMPLIANCE"},
    {"input": "この取引のECCNが5E002に該当するか判断して", "expected_intent": "CLASSIFY_ITEM"},
]

async def run_ab_test():
    results = []
    for case in TEST_CASES:
        local_result = await classify_intent_local(case["input"])
        api_result = await classify_intent_api_fallback(case["input"])

        match = local_result.get("intent") == api_result.get("intent")
        correct_local = local_result.get("intent") == case["expected_intent"]
        correct_api = api_result.get("intent") == case["expected_intent"]

        results.append({
            "input": case["input"],
            "expected": case["expected_intent"],
            "local_intent": local_result.get("intent"),
            "local_confidence": local_result.get("confidence"),
            "api_intent": api_result.get("intent"),
            "local_api_match": match,
            "local_correct": correct_local,
            "api_correct": correct_api,
        })

    # サマリ出力
    total = len(results)
    local_acc = sum(r["local_correct"] for r in results) / total
    api_acc = sum(r["api_correct"] for r in results) / total
    agreement = sum(r["local_api_match"] for r in results) / total

    print(f"\n=== ABテスト結果 ===")
    print(f"Local精度:   {local_acc:.1%}  ({sum(r['local_correct'] for r in results)}/{total})")
    print(f"API精度:     {api_acc:.1%}  ({sum(r['api_correct'] for r in results)}/{total})")
    print(f"Local-API一致率: {agreement:.1%}")
    print(f"\n--- 詳細 ---")
    for r in results:
        status = "✅" if r["local_correct"] else "❌"
        print(f"{status} [{r['expected']}] Local={r['local_intent']}({r['local_confidence']:.2f}) API={r['api_intent']}")
        print(f"   入力: {r['input']}")

    # 判定基準
    print(f"\n=== 移行判定 ===")
    if local_acc >= 0.90 and agreement >= 0.85:
        print("✅ 移行OK: Local精度90%以上 かつ API一致率85%以上")
    else:
        print("⚠️  要検討: テストケース追加またはプロンプト調整を推奨")

    return results

if __name__ == "__main__":
    asyncio.run(run_ab_test())
```

---

## 6. 検証フロー（Claude Codeへの作業指示）

```
[ ] Step 1: Ollama + Qwen2.5-14B セットアップ
    - brew install ollama
    - ollama pull qwen2.5:14b
    - ollama serve でサービス起動確認

[ ] Step 2: llm_router.py を所定のパスに配置
    - 既存のLLMクライアント実装と競合がないか確認
    - 既存のimport構造に合わせてパスを調整

[ ] Step 3: ABテスト実行
    - python dap/ab_test_intent.py
    - Local精度 >= 90% かつ API一致率 >= 85% を確認

[ ] Step 4: 精度基準を満たした場合
    - 既存のStage 1呼び出し箇所を classify_intent_local() に差し替え
    - 既存のStage 3呼び出し箇所を generate_action_local() に差し替え
    - run_dap_pipeline() をエンドポイントから呼び出す形に統合

[ ] Step 5: 精度基準を満たさない場合
    - INTENT_SYSTEM_PROMPT のインテントラベル・Few-shot例を追加調整
    - テストケースを実際の利用ログから補充して再テスト
```

---

## 7. 注意事項・既知リスク

| リスク | 対策 |
|---|---|
| Ollamaの初回モデルロード時間（〜10秒） | サーバー起動時にwarm-up推論を1回実行しておく |
| JSON出力の不安定（稀に余分なテキストが混入） | `format="json"` オプション + try/catch + APIフォールバックで対処済み |
| 日本語の固有表現・略語（外為法・EAR等） | プロンプトにドメイン用語を明示。精度不足時はFew-shot例を追加 |
| Ollama サービスが落ちていた場合 | フォールバックでHaiku APIに自動切り替えする実装済み |
| Stage 2のモデル名変更 | `claude-sonnet-4-6` を使用。変更時は `llm_router.py` の定数を更新 |

---

## 8. 将来の移行ステップ（Phase 2以降）

```
Phase 2（Stage 3 JSON生成の安定確認後）:
  → スキーマ固定アクションから順次ローカル化完了を宣言
  → Claude Haiku API の Stage 3 利用を廃止

Phase 3（利用ログ蓄積後）:
  → 外為法・EAR特化のファインチューニング検討
  → Stage 2の一部をローカルFine-tunedモデルで代替評価
```

---

*以上。検証完了後、このドキュメントのチェックリストを更新して記録に残すこと。*
