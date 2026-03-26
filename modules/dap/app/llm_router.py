"""
dap.app.llm_router
==================
ローカル LLM (Qwen2.5-14B / Ollama) と Claude API の Hybrid ルーター。

設計方針:
    - Stage 1: 意図分類 → ローカル Qwen2.5-14B (Ollama)
    - Stage 2 (複雑ケースのみ): 通常 chat() の Sonnet パスへ委譲
    - Stage 3: シンプルケースのみローカルで reply+actions+choices 生成

信頼スコアによる動的ルーティング:
    confidence >= CONFIDENCE_THRESHOLD かつ SIMPLE_INTENTS に属する
    → ローカル完結 (Stage 3 もローカル)
    それ以外 → Sonnet フルパスへ (呼び出し元の chat() がそのまま処理)

フォールバック設計 (CX 重視):
    - Ollama 未起動 / JSON パース失敗 → 即座に Sonnet パスへ透過フォールバック
    - ユーザーには一切エラーを見せない
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── モデル設定 ──────────────────────────────────────────────────────────────
_LOCAL_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")
_OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

# ローカルパスを使う意図ラベル (複雑推論不要なもののみ)
SIMPLE_INTENTS = frozenset([
    "NAVIGATE_UI",
    "EXPLAIN_UI",
    "SAVE_TRANSACTION",
])

# このスコア未満 or SIMPLE_INTENTS 外 → Sonnet フルパスへ
CONFIDENCE_THRESHOLD = float(os.environ.get("LOCAL_LLM_CONFIDENCE_THRESHOLD", "0.85"))

# ── Ollama 利用可否フラグ (起動時ヘルスチェックで設定) ─────────────────────
_ollama_available: bool = False
_ollama_client: Any = None  # ollama.AsyncClient


def init_ollama() -> None:
    """
    サーバー起動時に呼び出す。
    Ollama の疎通確認 + ウォームアップ推論を実行し、
    _ollama_available フラグを更新する。
    """
    global _ollama_available, _ollama_client
    try:
        import ollama as _ollama_lib
        client = _ollama_lib.Client(host=_OLLAMA_BASE_URL)
        # モデルリストで疎通確認
        models = client.list()
        available_names = [m.model for m in (models.models or [])]
        if not any(_LOCAL_MODEL.split(":")[0] in n for n in available_names):
            logger.warning(
                f"[llm_router] Ollama 起動中だが {_LOCAL_MODEL} が見つかりません。"
                f"利用可能: {available_names}"
            )
            return
        # ウォームアップ (モデルをメモリに展開させる)
        client.chat(
            model=_LOCAL_MODEL,
            messages=[{"role": "user", "content": "ping"}],
            options={"num_predict": 1},
        )
        _ollama_client = client
        _ollama_available = True
        logger.info(f"[llm_router] Ollama ウォームアップ完了: {_LOCAL_MODEL}")
    except Exception as e:
        logger.warning(f"[llm_router] Ollama 初期化失敗 (Sonnet フォールバック有効): {e}")
        _ollama_available = False


def is_local_llm_available() -> bool:
    """ローカル LLM が使用可能かどうかを返す"""
    return _ollama_available


# ── Stage 1: 意図分類プロンプト ────────────────────────────────────────────

_INTENT_SYSTEM_PROMPT = """\
あなたは輸出管理コンプライアンスシステムのアシスタントです。
ユーザーの発話を分類し、以下のJSON形式のみで回答してください。JSON以外は絶対に出力しないこと。

出力フォーマット:
{
  "intent": "<インテントラベル>",
  "confidence": <0.0〜1.0>,
  "requires_complex_reasoning": <true/false>
}

インテントラベル一覧:
- NAVIGATE_UI: 画面遷移・ページ移動・ボタンの場所を聞く
- EXPLAIN_UI: この画面の使い方・操作手順を聞く（規制の中身は不要）
- SAVE_TRANSACTION: 取引・案件の保存・登録・作成
- CLASSIFY_ITEM: 品目の該非判定・ECCN/外為法コードの分類
- CHECK_COMPLIANCE: 輸出許可要否・コンプライアンス確認・リスク判断
- EXPLAIN_REGULATION: 外為法・EAR・規制の内容を詳しく聞く
- SEARCH_ECCN: ECCNコード・外為法項番の検索・確認
- AGENT_START: 対話形式の該非判定エージェントを起動したい
- UNKNOWN: 判定不能

requires_complex_reasoning は、外為法解釈・規制判断・リスク評価が必要な場合のみ true。
画面操作・UI案内・一般的な手順説明は false。"""


async def classify_intent(
    message: str,
    context_summary: dict,
) -> dict:
    """
    Stage 1: ローカル Qwen2.5-14B で意図分類を実行する。

    Args:
        message: ユーザー発話
        context_summary: {module_name, page_path, persona_level} の要約

    Returns:
        {"intent": str, "confidence": float, "requires_complex_reasoning": bool}
        失敗時は {"intent": "UNKNOWN", "confidence": 0.0, "requires_complex_reasoning": True}
    """
    if not _ollama_available or _ollama_client is None:
        return {"intent": "UNKNOWN", "confidence": 0.0, "requires_complex_reasoning": True}

    module_name = context_summary.get("module_name", "不明")
    page_path   = context_summary.get("page_path", "")
    persona     = context_summary.get("persona_level", "unknown")

    user_msg = (
        f"現在のモジュール: {module_name} ({page_path})\n"
        f"ユーザー習熟度: {persona}\n"
        f"発話: {message}"
    )

    try:
        response = _ollama_client.chat(
            model=_LOCAL_MODEL,
            messages=[
                {"role": "system", "content": _INTENT_SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            format="json",
            options={"temperature": 0.1, "num_predict": 128},
        )
        raw = response.message.content
        result = json.loads(raw)
        # 必須フィールドの検証
        if "intent" not in result or "confidence" not in result:
            raise ValueError("必須フィールド欠落")
        logger.info(
            f"[Stage1/Local] intent={result['intent']}, "
            f"conf={result.get('confidence', 0):.2f}, "
            f"complex={result.get('requires_complex_reasoning', False)}"
        )
        return result
    except Exception as e:
        logger.warning(f"[Stage1/Local] 分類失敗: {e}")
        return {"intent": "UNKNOWN", "confidence": 0.0, "requires_complex_reasoning": True}


# ── Stage 3: シンプルケースのローカル応答生成 ──────────────────────────────

_SIMPLE_RESPONSE_SYSTEM_PROMPT = """\
あなたは輸出管理システムのアシスタントです。
ユーザーの操作を案内する短い返答をJSON形式で生成してください。JSON以外は絶対に出力しないこと。

出力フォーマット:
{
  "reply": "<100字以内の口語体日本語。マークダウン(**や#)禁止>",
  "actions": [
    {"type": "highlight", "target": "<interactive_elements のラベルと完全一致>"}
  ],
  "choices": [
    {"label": "<15字以内>", "message": "<クリック時のメッセージ>"},
    {"label": "<15字以内>", "message": "<クリック時のメッセージ>"}
  ]
}

ルール:
- reply は必ず100字以内の口語体日本語
- actions は interactive_elements に一致するボタン/リンクがある場合のみ含める (なければ空配列)
- choices は必ず2〜3件 (現在のタスクの次ステップを反映)
- target は interactive_elements のラベルと完全一致させること"""


async def generate_simple_response(
    message: str,
    intent_result: dict,
    context_summary: dict,
) -> Optional[dict]:
    """
    Stage 3: シンプルインテント用のローカル応答生成。

    Returns:
        {"reply": str, "actions": list, "choices": list}
        失敗時は None (呼び出し元が Sonnet パスにフォールバック)
    """
    if not _ollama_available or _ollama_client is None:
        return None

    module_name   = context_summary.get("module_name", "不明")
    page_path     = context_summary.get("page_path", "")
    elements      = context_summary.get("interactive_elements", [])
    persona       = context_summary.get("persona_level", "unknown")
    el_list       = ", ".join(f'「{e}」' for e in elements[:10]) if elements else "なし"

    user_msg = (
        f"モジュール: {module_name} ({page_path})\n"
        f"画面上のボタン/リンク: {el_list}\n"
        f"ユーザー習熟度: {persona}\n"
        f"意図: {intent_result.get('intent')}\n"
        f"発話: {message}"
    )

    try:
        response = _ollama_client.chat(
            model=_LOCAL_MODEL,
            messages=[
                {"role": "system", "content": _SIMPLE_RESPONSE_SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            format="json",
            options={"temperature": 0.1, "num_predict": 512},
        )
        raw = response.message.content
        result = json.loads(raw)

        # 必須フィールド検証
        if not result.get("reply") or not result.get("choices"):
            raise ValueError("reply/choices 欠落")
        if len(result.get("choices", [])) < 2:
            raise ValueError("choices が 2件未満")

        logger.info(f"[Stage3/Local] intent={intent_result.get('intent')} → ローカル応答生成完了")
        return result

    except Exception as e:
        logger.warning(f"[Stage3/Local] 応答生成失敗 (Sonnet へフォールバック): {e}")
        return None


# ── ルーティング判定 ────────────────────────────────────────────────────────

def should_use_local(intent_result: dict) -> bool:
    """
    ローカルパスで処理すべきかどうかを判定する。

    条件:
        - Ollama が利用可能
        - confidence >= CONFIDENCE_THRESHOLD
        - intent が SIMPLE_INTENTS に含まれる
        - requires_complex_reasoning が False
    """
    if not _ollama_available:
        return False
    confidence = intent_result.get("confidence", 0.0)
    intent     = intent_result.get("intent", "UNKNOWN")
    complex_   = intent_result.get("requires_complex_reasoning", True)
    return (
        confidence >= CONFIDENCE_THRESHOLD
        and intent in SIMPLE_INTENTS
        and not complex_
    )
