"""
dap.app.llm_router
==================
キーワードルーティング × ローカル LLM (Qwen2.5-14B) Hybrid ルーター。

テスト結果に基づく設計決定:
    - Stage 1 はキーワードマッチング (LLM 不要・即時) に変更
      理由: LLM Stage 1 (~960ms) を追加すると simple path の総レイテンシが増える
    - Stage 3 は Qwen2.5:14b でストリーミング応答生成
      理由: 品質は Sonnet 相当・コスト $0・M4 Mac mini で ~2,500ms
    - 複雑ケースは Sonnet フルパスへ (変更なし)

ルーティング判定:
    SIMPLE_KEYWORDS に一致 → ローカル 14B で応答 (ストリーミング)
    COMPLEX_KEYWORDS に一致 → Sonnet へ直接 (overhead なし)
    どちらでもない        → Sonnet (安全側フォールバック)

フォールバック:
    Ollama 未起動 / JSON パース失敗 → Sonnet へ透過フォールバック
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import AsyncGenerator, Optional

logger = logging.getLogger(__name__)

# ── モデル設定 ──────────────────────────────────────────────────────────────
_LOCAL_MODEL    = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")
_OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

# ── キーワードルーティング定義 ──────────────────────────────────────────────

# これに一致 → ローカル 14B で処理 (UI操作・画面移動・手順説明)
SIMPLE_KEYWORDS: frozenset[str] = frozenset([
    "に移動", "に戻", "のページ", "画面はどこ", "どこにあ", "どこです",
    "操作方法", "使い方", "やり方", "手順", "何をすれば", "次は何",
    "次のステップ", "何から始め", "どうすれば",
    "保存", "登録する方法", "作成する方法",
    "ボタンはどこ", "クリック", "押せば",
    "この画面で何", "ここで何", "どの画面",
])

# これに一致 → Sonnet へ直接 (規制判断・法令解釈・リスク評価)
COMPLEX_KEYWORDS: frozenset[str] = frozenset([
    "ECCN", "外為法", "輸出令", "別表", "規制", "該非", "キャッチオール",
    "リスト規制", "EAR", "BIS", "みなし輸出", "デュアルユース",
    "許可", "規制対象", "リスク", "コンプライアンス", "違反",
    "制裁", "スクリーニング", "SDN", "Entity List",
    "判定", "審査", "第25条", "安全保障", "Wassenaar",
    "エージェント", "対話形式",
])


def route_request(message: str) -> str:
    """
    メッセージをキーワードで分類し、ルーティング先を返す。

    Returns:
        "local"  → Qwen2.5:14b ローカルで処理
        "sonnet" → Sonnet API へ委譲
    """
    # 複雑キーワードが1つでも含まれれば Sonnet 優先
    if any(kw in message for kw in COMPLEX_KEYWORDS):
        return "sonnet"
    # 単純キーワードが1つでも含まれればローカル
    if any(kw in message for kw in SIMPLE_KEYWORDS):
        return "local"
    # どちらでもなければ Sonnet (安全側)
    return "sonnet"


# ── Ollama 利用可否 ────────────────────────────────────────────────────────

_ollama_available: bool = False
_ollama_client   = None  # ollama.Client


def init_ollama() -> None:
    """
    サーバー起動時に呼び出す。
    Ollama の疎通確認とウォームアップを実行し _ollama_available を設定する。
    """
    global _ollama_available, _ollama_client
    try:
        import ollama as _lib
        client = _lib.Client(host=_OLLAMA_BASE_URL)
        models = client.list()
        available = [m.model for m in (models.models or [])]
        base = _LOCAL_MODEL.split(":")[0]
        if not any(base in n for n in available):
            logger.warning(f"[llm_router] {_LOCAL_MODEL} が見つかりません。利用可能: {available}")
            return
        # ウォームアップ (モデルを VRAM に展開)
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
    return _ollama_available


# ── ローカル応答生成プロンプト ──────────────────────────────────────────────

_LOCAL_RESPOND_SYSTEM = """\
あなたは輸出管理コンプライアンスシステムの操作アシスタントです。
以下のJSON形式のみで回答してください。JSON以外は絶対に出力しないこと。

{
  "reply": "100字以内の口語体日本語。マークダウン(**や#)禁止",
  "actions": [{"type": "highlight", "target": "ボタンラベル完全一致"}],
  "choices": [
    {"label": "15字以内", "message": "クリック時メッセージ"},
    {"label": "15字以内", "message": "メッセージ"}
  ]
}

ルール:
- reply は必ず100字以内の口語体日本語
- actions: interactive_elements のラベルと完全一致する場合のみ含める (なければ空配列)
- choices: 必ず2〜3件。現在画面でユーザーが次にすべき操作の提案を含める"""


async def generate_local_response(
    message:      str,
    context:      dict,
    stream:       bool = True,
) -> AsyncGenerator[str, None]:
    """
    Qwen2.5:14b でローカル応答を生成するジェネレーター。

    Yields:
        JSON 文字列のチャンク (stream=True) または完全 JSON (stream=False)

    使用例:
        async for chunk in generate_local_response(msg, ctx):
            send_to_client(chunk)

    Raises:
        RuntimeError: Ollama 未起動または生成失敗時 (呼び出し元で Sonnet へフォールバック)
    """
    if not _ollama_available or _ollama_client is None:
        raise RuntimeError("Ollama 未起動")

    module_name = context.get("module_name", "不明")
    page_path   = context.get("page_path", "")
    elements    = context.get("interactive_elements", [])
    persona     = context.get("persona_level", "unknown")
    el_list     = "、".join(f"「{e}」" for e in elements[:10]) if elements else "なし"

    user_msg = (
        f"現在のモジュール: {module_name} ({page_path})\n"
        f"画面上のボタン/リンク: {el_list}\n"
        f"ユーザー習熟度: {persona}\n"
        f"発話: {message}"
    )

    try:
        import ollama as _lib
        buffer = ""
        if stream:
            for chunk in _lib.Client(host=_OLLAMA_BASE_URL).chat(
                model=_LOCAL_MODEL,
                messages=[
                    {"role": "system", "content": _LOCAL_RESPOND_SYSTEM},
                    {"role": "user",   "content": user_msg},
                ],
                stream=True,
                options={"temperature": 0.1, "num_predict": 512},
            ):
                delta = chunk.message.content or ""
                buffer += delta
                yield delta
        else:
            resp = _lib.Client(host=_OLLAMA_BASE_URL).chat(
                model=_LOCAL_MODEL,
                messages=[
                    {"role": "system", "content": _LOCAL_RESPOND_SYSTEM},
                    {"role": "user",   "content": user_msg},
                ],
                format="json",
                options={"temperature": 0.1, "num_predict": 512},
            )
            buffer = resp.message.content or ""
            yield buffer

        # 完成した JSON を検証
        _validate_response(buffer)
        logger.info(f"[llm_router/local] ローカル応答生成完了 ({len(buffer)}文字)")

    except Exception as e:
        logger.warning(f"[llm_router/local] 生成失敗: {e}")
        raise RuntimeError(f"ローカル生成失敗: {e}") from e


def parse_local_response(raw: str) -> Optional[dict]:
    """
    ローカル生成の生テキストから JSON を抽出してパースする。
    マークダウンコードブロックや余分なテキストが混入しても対応する。

    Returns:
        パース結果 dict、または None (パース失敗)
    """
    # ```json ... ``` ブロックを除去
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    # 先頭の { から末尾の } まで抽出
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not m:
        return None
    try:
        result = json.loads(m.group())
        _validate_response(m.group())
        return result
    except (json.JSONDecodeError, ValueError):
        return None


def _validate_response(raw: str) -> None:
    """必須フィールドの存在を検証する。不正なら ValueError を raise。"""
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not data.get("reply"):
        raise ValueError("reply フィールド欠落")
    choices = data.get("choices", [])
    if len(choices) < 2:
        raise ValueError(f"choices が 2件未満: {len(choices)}件")
