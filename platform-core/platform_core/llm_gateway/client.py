"""
platform_core.llm_gateway.client
==================================
Anthropic SDK を使った BaseLLMBridge 実装。

モデル使い分け:
    Qwen2.5:7b (Ollama) → 質問文生成（$0・ローカル）  ← OLLAMA_QUESTION_GEN=true 時
    Claude Haiku         → 質問文生成（フォールバック）
    Claude Sonnet        → 最終判定レポート生成（高品質・詳細、常に Sonnet）
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from platform_core.agent.base_agent import BaseLLMBridge, MissingAttr

logger = logging.getLogger(__name__)

# Anthropic SDK はオプショナルインポート（未インストール環境でも起動できるよう）
try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

# Ollama はオプショナルインポート（未インストール環境でも起動できるよう）
try:
    import ollama as _ollama_lib
    _OLLAMA_AVAILABLE = True
except ImportError:
    _OLLAMA_AVAILABLE = False

_MODEL_HAIKU  = "claude-haiku-4-5-20251001"
_MODEL_SONNET = "claude-sonnet-4-6"
_OLLAMA_MODEL = os.environ.get("OLLAMA_QUESTION_MODEL", "qwen2.5:7b")
_OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
# OLLAMA_QUESTION_GEN=true で質問生成を Ollama 7B に切り替える
_USE_OLLAMA_FOR_QUESTIONS = os.environ.get("OLLAMA_QUESTION_GEN", "").lower() in ("1", "true", "yes")

_QUESTION_SYSTEM_PROMPT = """\
あなたは外為法・輸出管理の専門家として、輸出取引の担当者に必要な情報を確認しています。
確認する質問は1つだけにしてください。
質問は簡潔で具体的にし、担当者が数値や選択肢で答えやすい形にしてください。
余計な説明は不要です。"""

_REPORT_SYSTEM_PROMPT = """\
あなたは外為法・輸出管理の専門家として、以下の判定結果に基づいた
輸出審査レポートを作成します。
根拠を明確に示し、コンプライアンス担当者が監査に使える形式で記述してください。"""


class ClaudeLLMBridge(BaseLLMBridge):
    """
    Anthropic Claude API を使った LLM ブリッジ。

    api_key が未設定の場合は fallback モード（固定テンプレート）で動作するため、
    ローカル開発中でも Claude API なしでテストできる。
    """

    def __init__(
        self,
        api_key:      Optional[str] = None,
        model_haiku:  str           = _MODEL_HAIKU,
        model_sonnet: str           = _MODEL_SONNET,
    ):
        self._api_key      = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self._model_haiku  = model_haiku
        self._model_sonnet = model_sonnet
        self._client: Optional["anthropic.AsyncAnthropic"] = None

        if _ANTHROPIC_AVAILABLE and self._api_key:
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)

    # ── 質問文生成: Ollama 7B (優先) / Haiku (フォールバック) ──────────────

    async def generate_question(
        self, missing_attr: MissingAttr, context_dict: dict
    ) -> str:
        """
        MissingAttr（何を確認すべきか）を受け取り、自然言語の質問文を生成する。

        OLLAMA_QUESTION_GEN=true かつ Ollama が起動中 → Qwen2.5:7b（$0）
        それ以外 → Claude Haiku（フォールバック）
        """
        unit_hint = f"（単位: {missing_attr.unit}）" if missing_attr.unit else ""
        example_hint = f"（例: {missing_attr.example}）" if missing_attr.example else ""
        known_summary = self._summarize_known(context_dict)

        user_message = f"""\
確認が必要な情報: {missing_attr.label}{unit_hint}
その理由: {missing_attr.reason}
{example_hint}
これまでに判明している情報: {known_summary}

上記の情報を担当者に確認する質問を1つ生成してください。"""

        # ── Ollama 7B パス ───────────────────────────────────────────────
        if _USE_OLLAMA_FOR_QUESTIONS and _OLLAMA_AVAILABLE:
            try:
                result = await asyncio.to_thread(
                    self._ollama_generate_question, user_message
                )
                if result:
                    return result
            except Exception as e:
                logger.warning("[llm_gateway] Ollama 質問生成失敗、Haiku にフォールバック: %s", e)

        # ── Haiku フォールバック ─────────────────────────────────────────
        if not self._client:
            return self._fallback_question(missing_attr)

        try:
            resp = await self._client.messages.create(
                model=self._model_haiku,
                max_tokens=256,
                system=_QUESTION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            return resp.content[0].text.strip()
        except Exception:
            return self._fallback_question(missing_attr)

    @staticmethod
    def _ollama_generate_question(user_message: str) -> str:
        """Ollama 同期呼び出し（asyncio.to_thread 経由で非同期文脈から呼ぶ）"""
        client = _ollama_lib.Client(host=_OLLAMA_BASE_URL)
        resp = client.chat(
            model=_OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": _QUESTION_SYSTEM_PROMPT},
                {"role": "user",   "content": user_message},
            ],
            options={"temperature": 0.1, "num_predict": 128},
        )
        return (resp.message.content or "").strip()

    # ── Sonnet: 最終レポート生成 ────────────────────────────────────────────

    async def generate_report(
        self, context_dict: dict, result: dict
    ) -> str:
        """
        最終判定結果を受け取り、Sonnet を使って
        監査対応可能な自然言語レポートを生成する。
        """
        if not self._client:
            return self._fallback_report(result)

        controlled = result.get("controlled_items", [])
        pending    = result.get("pending_items", [])
        reasons    = result.get("reasons", [])

        reasons_text = "\n".join(
            f"- {r.get('title', r.get('domain_id', '?'))}: {r.get('explanation', '')}"
            for r in reasons
        ) or "（根拠情報なし）"

        user_message = f"""\
## 判定結果
- 規制対象項番: {', '.join(controlled) or 'なし'}
- 要確認項番: {', '.join(pending) or 'なし'}

## 判定根拠
{reasons_text}

## 確認済み属性
{self._summarize_known(context_dict)}

上記に基づき、輸出審査レポートを作成してください。"""

        try:
            resp = await self._client.messages.create(
                model=self._model_sonnet,
                max_tokens=1024,
                system=_REPORT_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            return resp.content[0].text.strip()
        except Exception:
            return self._fallback_report(result)

    # ── フォールバック（API なし）─────────────────────────────────────────

    @staticmethod
    def _fallback_question(missing_attr: MissingAttr) -> str:
        unit = f"（{missing_attr.unit}）" if missing_attr.unit else ""
        example = f"（例: {missing_attr.example}）" if missing_attr.example else ""
        return f"「{missing_attr.label}」{unit}をご確認ください。{example}"

    @staticmethod
    def _fallback_report(result: dict) -> str:
        controlled = result.get("controlled_items", [])
        if controlled:
            return f"規制対象項番が検出されました: {', '.join(controlled)}。詳細な審査が必要です。"
        return "現時点で規制対象項番は検出されていません。"

    @staticmethod
    def _summarize_known(context_dict: dict) -> str:
        # known_attributes（汎用）＋ 専用フィールドを統合
        known: dict = dict(context_dict.get("known_attributes", {}))
        for key in ("end_use_type", "end_user_type", "destination_country"):
            val = context_dict.get(key)
            if val and val not in ("unknown", None):
                known[key] = val
        if not known:
            return "（なし）"
        parts = [f"{k}: {v}" for k, v in list(known.items())[:8]]
        return " / ".join(parts)
