"""
DAP Chat Router — Claude API を使った会話型インターフェース

方針（現フェーズ）:
  - 規制・法令データの RAG は行わない
  - ユーザーの意図理解・業務文脈の把握にフォーカス
  - 現在ページ + フォーム入力値 + インタラクティブ要素をコンテキストとして Claude に渡す
  - 構造化 JSON レスポンス（reply / actions / choices）で UI 操作まで完結させる
  - サーバーサイドセッションでページをまたいで会話を継続
"""
from __future__ import annotations

import json as _json
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import DapChatConfig

# .env フォールバック（start.sh 経由でない単体起動時に ANTHROPIC_API_KEY を補完）
try:
    from dotenv import load_dotenv as _load_dotenv
    _env_file = Path(__file__).resolve().parents[4] / ".env"
    if _env_file.exists():
        _load_dotenv(_env_file, override=False)
except ImportError:
    pass

router = APIRouter(tags=["chat"])

# ── モジュール名マッピング（port → 表示名）────────────────────────────
_MODULE_MAP: dict[str, str] = {
    "8001": "AI 該非判定（ai_validation）",
    "8002": "品目管理（ai_classification）",
    "8003": "R&D リスク管理（rnd_assessment）",
    "8004": "特許検索（patent_search）",
    "8005": "スクリーニング（screening）",
    "8006": "HS コード判定（hs_classifier）",
    "8010": "DAP 管理画面",
}

# ── サーバーサイド・セッションストア ─────────────────────────────────
# {session_id: {"history": [...], "task": str}}
# OrderedDict で LRU 的に最大 200 セッションを保持
_SESSION_STORE: OrderedDict[str, dict] = OrderedDict()
_SESSION_MAX = 200
_SESSION_MAX_HISTORY = 40  # 最大 20 往復


def _get_session(session_id: str) -> dict:
    if session_id in _SESSION_STORE:
        _SESSION_STORE.move_to_end(session_id)
        return _SESSION_STORE[session_id]
    return {"history": [], "task": ""}


def _save_session(session_id: str, data: dict) -> None:
    _SESSION_STORE[session_id] = data
    _SESSION_STORE.move_to_end(session_id)
    while len(_SESSION_STORE) > _SESSION_MAX:
        _SESSION_STORE.popitem(last=False)


# ── Pydantic スキーマ ────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    history: list[dict[str, Any]] = []   # 後方互換: クライアント側履歴
    context: dict[str, Any] = {}          # {port, page_path, form_fields, interactive_elements, ...}
    session_id: Optional[str] = None      # クロスページセッション用


class ChatResponse(BaseModel):
    reply: str
    actions: list[dict[str, Any]] = []   # [{type, target, value?, ...}]
    choices: list[dict[str, Any]] = []   # [{label, message}]


# ── モジュール別デフォルト choices（Claude が省略した場合のフォールバック）──────
_DEFAULT_CHOICES: dict[str, list[dict]] = {
    "8001": [
        {"label": "判定結果を確認",    "message": "判定結果の見方を教えてください"},
        {"label": "必要書類を確認",    "message": "この審査で必要な書類を教えてください"},
        {"label": "次のステップ",      "message": "判定後の次のステップを教えてください"},
    ],
    "8002": [
        {"label": "用途概要の書き方",  "message": "用途概要の入力例を教えてください"},
        {"label": "AI判定を依頼",     "message": "AI該非判定を依頼するにはどうすればいいですか"},
        {"label": "HS コードを確認",  "message": "HSコード判定の依頼方法を教えてください"},
    ],
    "8003": [
        {"label": "入力項目を確認",    "message": "入力必須項目を教えてください"},
        {"label": "AI判定を実行",     "message": "AI該非判定を実行するにはどうすればいいですか"},
        {"label": "審査フローを確認",  "message": "R&Dリスク管理の審査フローを教えてください"},
    ],
    "8004": [
        {"label": "検索方法を確認",    "message": "特許検索の操作方法を教えてください"},
        {"label": "結果の見方",       "message": "検索結果の見方を教えてください"},
    ],
    "8005": [
        {"label": "スクリーニング方法", "message": "スクリーニングの実行方法を教えてください"},
        {"label": "結果の確認",       "message": "スクリーニング結果の確認方法を教えてください"},
    ],
}
_DEFAULT_CHOICES_FALLBACK = [
    {"label": "操作方法を教えて",  "message": "この画面の操作方法を教えてください"},
    {"label": "次のステップ",      "message": "次に何をすればいいですか"},
]


# ── チャットウィジェット設定スキーマ ──────────────────────────────────────
class ChatConfigUpdate(BaseModel):
    enabled: int = 1
    prompt_supplement: str = ""


# ── System prompt 構築 ───────────────────────────────────────────────
def _build_system_prompt(ctx: dict[str, Any], prompt_supplement: str = "") -> str:
    port         = str(ctx.get("port", ""))
    module_name  = _MODULE_MAP.get(port, "不明なモジュール")
    page_path    = ctx.get("page_path", "（不明）")
    current_task = ctx.get("current_task", "")

    # フォーム入力値（最大 8 フィールド）
    fields: dict[str, str] = ctx.get("form_fields", {})
    if fields:
        lines = [f"  ・{k}: {v}" for k, v in list(fields.items())[:8]]
        form_block = "フォーム入力状況:\n" + "\n".join(lines)
    else:
        form_block = "フォーム入力状況: （未入力 or 取得なし）"

    # インタラクティブ要素（ボタン・リンク）
    elements: list[dict] = ctx.get("interactive_elements", [])
    if elements:
        el_lines = [f"  ・{e.get('label', '')}" for e in elements[:20]]
        elements_block = "画面上のボタン・リンク:\n" + "\n".join(el_lines)
    else:
        elements_block = ""

    extra_note = ctx.get("extra_note", "")
    extra_block = f"\n追加情報: {extra_note}" if extra_note else ""

    task_block = f"\n【進行中のタスク】\n{current_task}" if current_task else ""

    return f"""あなたは輸出管理コンプライアンス業務の AI アシスタントです。
ユーザーが今この画面で何をしようとしているかを理解し、業務フローの文脈に沿って行動まで完結させてください。

【業務フロー全体像】
このシステムは以下のモジュール連鎖で構成されています:

1. R&D リスク管理（port 8003, http://localhost:8003）
   研究開発案件のリスク審査・プロジェクト登録
   フロー: 案件作成 → プロファイル入力（用途要件・需要者要件）→ AI審査 → 品目管理へワンクリック登録
   入力必須: 用途要件（工程/装置/性能/最終使用地）、需要者要件（法人名/場所/第三者提供の有無）

2. 品目管理（port 8002, http://localhost:8002）
   輸出品目の登録・分類・AI判定依頼
   フロー: 品目登録（品目コード・名称・仕様）→ 用途概要入力 → HSコード判定（8006連携）→ AI該非判定を依頼
   注意: 用途概要が不十分だと AI 判定の精度が落ちる。工程/装置/性能/最終使用地の4要素を含めること

3. AI 該非判定（port 8001, http://localhost:8001）
   外為法マトリクス照合・案件審査
   フロー: 案件作成 → スクリーニング実行（8005連携）→ AI判定実行 → 結果確認 → CSV/PDF出力
   結果の読み方: intersection=要注意(黄)、core_only=直接リストヒット(青)、expanded_only=低リスク(灰)

4. スクリーニング（port 8005, http://localhost:8005）
   取引先の制裁リストチェック
   注意: 企業名は英語正式法人名で入力するとマッチ精度が上がる

5. 特許検索（port 8004）: 技術的先行技術・競合特許の調査
{task_block}

【現在の状況】
モジュール: {module_name}（port {port}）
ページ: {page_path}
{form_block}
{elements_block}{extra_block}

【行動指針】
- ユーザーの発言から「最終的にやりたいこと」を推測し、そのゴールへの最短経路を案内する
- 現在のページにない機能が必要な場合は、どのモジュール（URLも含む）に移動すべきか具体的に案内する
- choices は「今すぐ次にやること」を反映させる。汎用的な質問より、ユーザーのタスクの次ステップを優先する
- interactive_elements に該当するボタン/リンクがあれば必ず actions に含める

【respond ツールの使い方 — 以下の例を参考にすること】

例1: R&D画面、ユーザー「新規プロジェクトを登録したい」、interactive_elements に「+ 新規プロジェクト」
  reply: 「+ 新規プロジェクト」から始めましょう。タイトル・担当者を入力後、用途要件と需要者要件のプロファイルを作成します。
  actions: [{{"type": "highlight", "target": "+ 新規プロジェクト"}}]
  choices: [{{"label": "用途要件の書き方", "message": "用途要件に何を書けばいいですか"}}, {{"label": "品目管理への流れ", "message": "R&D審査完了後に品目管理へ登録するにはどうしますか"}}]

例2: 品目管理、ユーザー「AI判定を依頼したい」、interactive_elements に「外部AIへ判定依頼」
  reply: 用途概要に工程・装置・性能・最終使用地の4要素が揃っていれば「外部AIへ判定依頼」から進められます。
  actions: [{{"type": "highlight", "target": "外部AIへ判定依頼"}}]
  choices: [{{"label": "用途概要の入力例", "message": "用途概要の入力例を教えてください"}}, {{"label": "判定完了後の確認先", "message": "AI判定が完了したらどこで結果を確認できますか"}}]

例3: AI判定画面、ユーザー「スクリーニングをしたい」、interactive_elements に「スクリーニングを実行」
  reply: 「スクリーニングを実行」から取引先の制裁リストチェックができます。企業名は英語正式法人名が精度向上につながります。
  actions: [{{"type": "highlight", "target": "スクリーニングを実行"}}]
  choices: [{{"label": "結果の読み方", "message": "match/possible_matchはどういう意味ですか"}}, {{"label": "AI判定を実行", "message": "スクリーニング後にAI判定を実行するにはどうしますか"}}]

例4: R&D画面、ユーザー「品目管理に登録したい」、プロファイルにAI判定結果あり
  reply: AI審査が完了していれば「品目管理へ登録」ボタンでワンクリック登録できます。登録後は http://localhost:8002 で品目の詳細を確認できます。
  actions: [{{"type": "highlight", "target": "品目管理へ登録"}}]
  choices: [{{"label": "品目管理に移動", "message": "品目管理（8002）でやること一覧を教えてください"}}, {{"label": "登録後の流れ", "message": "品目管理登録後にAI該非判定を依頼するにはどうしますか"}}]

【ルール】
- reply: マークダウン禁止（**や# など使わない）。100字以内の口語体日本語。
- actions: 「画面上のボタン・リンク」リストの要素が該当する場合は必ず含める。target は完全一致。
- choices: 必ず2〜3件含める（省略・空配列禁止）。現在のタスクの次ステップを反映させる。
- 法令解釈・規制判断はしない（専門家確認を案内）""" + (
        f"\n\n【管理者補足情報】\n{prompt_supplement}" if prompt_supplement.strip() else ""
    )


# ── エンドポイント ────────────────────────────────────────────────────
@router.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, db: Session = Depends(get_db)) -> ChatResponse:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY が設定されていません")

    # モジュール別チャット設定を確認
    port = str(req.context.get("port", ""))
    chat_cfg = db.query(DapChatConfig).filter(DapChatConfig.port == port).one_or_none()
    if chat_cfg and chat_cfg.enabled == 0:
        return ChatResponse(
            reply="このモジュールではチャットアシスタントは無効になっています。",
            actions=[],
            choices=[],
        )
    prompt_supplement = chat_cfg.prompt_supplement if chat_cfg else ""

    client = anthropic.Anthropic(api_key=api_key)

    # セッション履歴を取得（session_id があればサーバー側優先、なければクライアント送信分）
    session_data: dict = {}
    if req.session_id:
        session_data = _get_session(req.session_id)

    history = session_data.get("history") or req.history
    history = list(history)[-_SESSION_MAX_HISTORY:]

    # current_task をコンテキストに追加
    ctx = dict(req.context)
    ctx["current_task"] = session_data.get("task", "")
    port = str(ctx.get("port", ""))

    # role の正規化
    valid_roles = {"user", "assistant"}
    messages = [m for m in history if m.get("role") in valid_roles]
    messages.append({"role": "user", "content": req.message})

    # ツール定義（tool_use で構造化出力を強制）
    _RESPOND_TOOL = {
        "name": "respond",
        "description": (
            "ユーザーへの返答を構造化フォーマットで返す。"
            "reply はマークダウン禁止・100字以内。"
            "interactive_elements リストに一致する要素があれば必ず actions に highlight を含める。"
            "choices は常に2〜3件含めること（必須）。現在のタスクの次ステップを反映させる。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reply": {
                    "type": "string",
                    "description": "ユーザーへの返答テキスト。マークダウン(**や#など)禁止。100字以内の口語体日本語。",
                },
                "actions": {
                    "type": "array",
                    "description": (
                        "画面上で実行するアクション（最大2件）。"
                        "interactive_elements に一致するボタン/リンクがあれば必ず highlight を入れる。"
                        "target は interactive_elements のラベルと完全一致させること。"
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "type":   {"type": "string", "enum": ["highlight", "fill_field"]},
                            "target": {"type": "string", "description": "対象要素のテキスト（interactive_elements のラベルと完全一致）"},
                            "value":  {"type": "string", "description": "fill_field 時に転記する値"},
                        },
                        "required": ["type", "target"],
                    },
                },
                "choices": {
                    "type": "array",
                    "description": "次のステップとして提示する選択肢。必ず2件以上・3件以下含めること。現在のタスクの次ステップを優先する。",
                    "minItems": 2,
                    "items": {
                        "type": "object",
                        "properties": {
                            "label":   {"type": "string", "description": "ボタン表示テキスト（15字以内）"},
                            "message": {"type": "string", "description": "クリック時に送信するメッセージ"},
                        },
                        "required": ["label", "message"],
                    },
                },
            },
            "required": ["reply", "choices"],
        },
    }

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=_build_system_prompt(ctx, prompt_supplement),
            messages=messages,
            tools=[_RESPOND_TOOL],
            tool_choice={"type": "tool", "name": "respond"},
        )
    except anthropic.APIError as e:
        raise HTTPException(status_code=502, detail=f"Claude API エラー: {e}")

    # tool_use ブロックから入力を取得
    default_choices = _DEFAULT_CHOICES.get(port, _DEFAULT_CHOICES_FALLBACK)

    reply_text = ""
    result_actions: list = []
    result_choices: list = []

    for block in resp.content:
        if block.type == "tool_use" and block.name == "respond":
            inp = block.input or {}
            reply_text = inp.get("reply", "")
            result_actions = inp.get("actions") or []
            result_choices = inp.get("choices") or []
            if len(result_choices) < 2:
                result_choices = default_choices
            break

    # フォールバック（tool_use が返らなかった場合）
    if not reply_text:
        reply_text = next((b.text for b in resp.content if hasattr(b, "text")), "")
        result_choices = default_choices

    # セッションに保存
    if req.session_id:
        history.append({"role": "user", "content": req.message})
        history.append({"role": "assistant", "content": reply_text})
        session_data["history"] = history[-_SESSION_MAX_HISTORY:]
        _save_session(req.session_id, session_data)

    return ChatResponse(
        reply=reply_text,
        actions=result_actions,
        choices=result_choices,
    )


@router.get("/api/chat/session/{session_id}")
async def get_session_info(session_id: str) -> dict:
    """セッションの存在確認（クロスページ継続インジケーター用）"""
    data = _get_session(session_id)
    return {
        "has_history": len(data.get("history", [])) > 0,
        "task": data.get("task", ""),
    }


@router.delete("/api/chat/session/{session_id}")
async def clear_session(session_id: str) -> dict:
    """セッション履歴を削除（会話クリア時に呼ばれる）"""
    _SESSION_STORE.pop(session_id, None)
    return {"ok": True}


# ── チャットウィジェット設定 CRUD ─────────────────────────────────────────────
_CONFIG_PORTS: list[dict] = [
    {"port": "8000", "label": "プラットフォーム"},
    {"port": "8001", "label": "AI 該非判定"},
    {"port": "8002", "label": "品目管理"},
    {"port": "8003", "label": "R&D リスク管理"},
    {"port": "8004", "label": "特許検索"},
    {"port": "8005", "label": "スクリーニング"},
    {"port": "8006", "label": "HS コード判定"},
]


@router.get("/api/chat/app-configs")
def get_chat_app_configs(db: Session = Depends(get_db)) -> list:
    """モジュール別チャットウィジェット設定を一覧で返す"""
    rows = db.query(DapChatConfig).all()
    config_map = {r.port: r for r in rows}
    result = []
    for m in _CONFIG_PORTS:
        cfg = config_map.get(m["port"])
        result.append({
            "port": m["port"],
            "label": m["label"],
            "enabled": cfg.enabled if cfg else 1,
            "prompt_supplement": cfg.prompt_supplement if cfg else "",
        })
    return result


@router.put("/api/chat/app-configs/{port}")
def update_chat_app_config(
    port: str, payload: ChatConfigUpdate, db: Session = Depends(get_db)
) -> dict:
    """モジュール別チャットウィジェット設定を更新（upsert）"""
    from datetime import datetime as _dt
    cfg = db.query(DapChatConfig).filter(DapChatConfig.port == port).one_or_none()
    if cfg:
        cfg.enabled = payload.enabled
        cfg.prompt_supplement = payload.prompt_supplement
        cfg.updated_at = _dt.utcnow()
    else:
        label = next((m["label"] for m in _CONFIG_PORTS if m["port"] == port), "")
        cfg = DapChatConfig(
            port=port,
            label=label,
            enabled=payload.enabled,
            prompt_supplement=payload.prompt_supplement,
        )
        db.add(cfg)
    db.commit()
    return {"ok": True}
