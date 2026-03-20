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
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import DapChatConfig

_PLATFORM_URL = os.environ.get("MODULE_PLATFORM_URL", "http://localhost:8000")

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

【規制インテリジェンス — 主要規制の要点】

■ 外為法（FEFTA）体系
・「リスト規制」（輸出貿易管理令別表第1）と「キャッチオール規制」（大量破壊兵器・通常兵器）の2本立て
・みなし輸出管理（2022年5月改正）: 非居住者から「重要な影響」を受ける居住者への技術提供を規制。以下3カテゴリで影響が推定される:
  - カテゴリ1: 非関連外国法人との二重雇用
  - カテゴリ2: 外国法人からの技術使用に関する指示への従属
  - カテゴリ3: 配偶者・近親者が非居住者かつ在日10年未満（条件付き）
・セキュリティクリアランス制度（2025年5月17日施行）: 重要経済安保情報19分野。民間人も対象。違反は最大5年懲役

■ 経済安全保障推進法（ESPA, 2022年）4本柱
1. サプライチェーン強靱化（重要物資の指定・備蓄）
2. 基幹インフラ安全性確保（14分野の設備・システム事前審査）
3. 先端技術開発支援（政府R&Dプログラム・技術保護義務）
4. 特許出願非公開（安全保障上重要な特許の外国出願禁止・公開停止）

■ 米国 EAR/ECCN（BIS）
・ECCN番号（例: 3E001）で管理。NS・AT・SL等の理由コードごとに許可要件が異なる
・みなし再輸出: 米国技術を保有する非米国企業が別の外国人に開示する行為も規制対象
・2022年10月規制: 先端半導体・AI・スパコンへの包括的規制強化。日本・オランダと協調

■ Wassenaar アレンジメント
・通常兵器・デュアルユース品目の多国間レジーム（42カ国）。外為法別表第1の多くがWassenaarリストに対応

■ 制裁スクリーニングの対象リスト
・OFAC SDN（米国財務省）、BIS Entity List（商務省）、METI 外国ユーザーリスト、EU 統合制裁リスト
・50%ルール（BIS）: SDN指定企業が50%以上保有する企業も同等の制裁対象
・スクリーニング結果: match=確定ヒット、possible_match=要確認、no_match=問題なし

■ 4象限戦略フレームワーク（技術主権価値 × 規制感度）
・要塞技術（高主権×高規制）: 特許非公開の検討・同盟国限定共有が必要
・無防備な至宝（高主権×低規制）: 先行IP化・貿易秘密の多層保護が急務（規制強化前に対策を）
・コンプライアンス負荷（低主権×高規制）: 効率的なコンプライアンス自動化・ライセンス活用
・開放領域（低主権×低規制）: グローバル展開を優先。サプライチェーン分散リスクは注意

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
- 規制に関する質問には上記インテリジェンス情報を活用して参考情報を提供する

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

【NeuroSymbolic 該非判定エージェント（重要機能）】
- ユーザーが「該非判定エージェント」「NeuroSymbolicエージェント」「対話形式の判定」「AI質問形式で判定」などと言った場合は、
  actions に {{"type": "start_agent", "target": "", "initial_query": "<品目の説明>", "transaction_id": <番号またはnull>}} を含める
- initial_query には品目名・仕様・用途などユーザーが述べた情報をそのまま渡す
- transaction_id はコンテキストから判断できる場合のみ数値で設定（不明な場合は省略）
- start_agent を含む場合、reply はエージェント起動の説明（「NeuroSymbolicエージェントを起動します。対話形式で外為法・EAR該非判定を行います」など）にする
- エージェントが起動すると、以降の返答は直接エージェントから来る（Claude を通さない）

【ルール】
- reply: マークダウン禁止（**や# など使わない）。100字以内の口語体日本語。
- actions: 「画面上のボタン・リンク」リストの要素が該当する場合は必ず含める。target は完全一致。
- choices: 必ず2〜3件含める（省略・空配列禁止）。現在のタスクの次ステップを反映させる。
- 規制の概要・判断参考情報は上記インテリジェンス情報をもとに提供可。最終的な法令解釈・規制判断は必ず専門家（法務・コンプライアンス担当者）への確認を案内する""" + (
        f"\n\n【管理者補足情報】\n{prompt_supplement}" if prompt_supplement.strip() else ""
    )


# ── エンドポイント ────────────────────────────────────────────────────
async def _call_agent_answer(agent_session_id: str, message: str) -> dict:
    """platform-core の agent API に回答を送信し、次の質問または判定完了を返す"""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{_PLATFORM_URL}/agent/sessions/{agent_session_id}/answer",
            json={"answer": message},
        )
        resp.raise_for_status()
        return resp.json()


async def _call_agent_judge(agent_session_id: str) -> dict:
    """最終判定を実行する"""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{_PLATFORM_URL}/agent/sessions/{agent_session_id}/judge",
        )
        resp.raise_for_status()
        return resp.json()


async def _start_agent_session(initial_query: str, transaction_id: Optional[int] = None) -> dict:
    """platform-core にエージェントセッションを開始する"""
    payload: dict[str, Any] = {"initial_query": initial_query}
    if transaction_id is not None:
        payload["transaction_id"] = transaction_id
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{_PLATFORM_URL}/agent/sessions",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


def _format_agent_turn(agent_resp: dict) -> tuple[str, list[dict]]:
    """
    agent API レスポンスをチャット用 reply + choices に変換する。
    戻り値: (reply, choices)
    """
    if agent_resp.get("is_ready_for_judgment"):
        reply = (
            "必要な情報が揃いました。最終判定を実行します。"
            f"（絞り込み候補: {agent_resp.get('candidates_count', 0)}件）"
        )
        choices = [
            {"label": "判定を実行",     "message": "__hantei_execute_judge__"},
            {"label": "エージェント終了", "message": "__hantei_cancel__"},
        ]
    else:
        question = agent_resp.get("question", "次の質問を確認中...")
        count = agent_resp.get("candidates_count", "?")
        reply = f"{question}（候補 {count}件）"
        choices = [
            {"label": "わからない",     "message": "よくわかりません。一般的な回答を教えてください"},
            {"label": "該当なし",       "message": "該当しません"},
            {"label": "エージェント終了", "message": "__hantei_cancel__"},
        ]
    return reply, choices


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

    # ── NeuroSymbolic エージェントモード ────────────────────────────────────
    agent_session_id: Optional[str] = session_data.get("hantei_agent_session_id")
    if agent_session_id:
        message = req.message.strip()

        # キャンセル
        if message == "__hantei_cancel__":
            session_data["hantei_agent_session_id"] = None
            if req.session_id:
                _save_session(req.session_id, session_data)
            return ChatResponse(
                reply="NeuroSymbolic 該非判定エージェントを終了しました。",
                actions=[],
                choices=[
                    {"label": "新規判定",     "message": "新しい品目で該非判定エージェントを起動したい"},
                    {"label": "通常操作へ戻る", "message": "次にすることを教えてください"},
                ],
            )

        try:
            if message == "__hantei_execute_judge__":
                judge_data = await _call_agent_judge(agent_session_id)
                session_data["hantei_agent_session_id"] = None
                if req.session_id:
                    _save_session(req.session_id, session_data)
                status = judge_data.get("overall_status", "pending")
                summary = judge_data.get("summary", "")
                controlled = ", ".join(judge_data.get("controlled_items", [])) or "なし"
                reply = (
                    f"判定完了。総合ステータス: {status}。"
                    f"規制対象項番: {controlled}。"
                    f"{summary[:100] if summary else ''}"
                )
                choices = [
                    {"label": "判定詳細を確認", "message": "判定結果の詳細を教えてください"},
                    {"label": "新規判定",       "message": "新しい品目で該非判定エージェントを起動したい"},
                ]
                return ChatResponse(reply=reply, actions=[], choices=choices)

            # 通常回答転送
            agent_resp = await _call_agent_answer(agent_session_id, message)
            reply, choices = _format_agent_turn(agent_resp)

            if req.session_id:
                history_buf = session_data.get("history", [])
                history_buf.append({"role": "user", "content": message})
                history_buf.append({"role": "assistant", "content": reply})
                session_data["history"] = history_buf[-_SESSION_MAX_HISTORY:]
                _save_session(req.session_id, session_data)

            return ChatResponse(reply=reply, actions=[], choices=choices)

        except httpx.HTTPError as e:
            # エージェント API エラー: エージェントモードを解除して通常モードへ
            session_data["hantei_agent_session_id"] = None
            if req.session_id:
                _save_session(req.session_id, session_data)
            return ChatResponse(
                reply=f"エージェント接続エラーが発生しました。通常モードに戻ります。（{e}）",
                actions=[],
                choices=[
                    {"label": "再起動",         "message": "該非判定エージェントをもう一度起動したい"},
                    {"label": "通常操作を続ける", "message": "次にすることを教えてください"},
                ],
            )
    # ── エージェントモードここまで ───────────────────────────────────────────

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
                            "type":   {"type": "string", "enum": ["highlight", "fill_field", "start_agent"]},
                            "target": {"type": "string", "description": "対象要素のテキスト（interactive_elements のラベルと完全一致）。start_agent の場合は空文字列でよい"},
                            "value":  {"type": "string", "description": "fill_field 時に転記する値"},
                            "initial_query": {"type": "string", "description": "start_agent 時: NeuroSymbolic 該非判定エージェントへの初期クエリ（品目・取引の説明）"},
                            "transaction_id": {"type": "integer", "description": "start_agent 時: 紐付ける Transaction ID（ai_validation モジュール）"},
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

    # ── start_agent action 検出: エージェントセッションを開始 ─────────────────
    start_agent_action = next(
        (a for a in result_actions if a.get("type") == "start_agent"), None
    )
    if start_agent_action and req.session_id:
        initial_query = start_agent_action.get("initial_query", req.message)
        tx_id_raw = start_agent_action.get("transaction_id")
        tx_id = int(tx_id_raw) if tx_id_raw else None
        try:
            agent_start = await _start_agent_session(initial_query, transaction_id=tx_id)
            new_agent_session_id = agent_start["session_id"]
            session_data["hantei_agent_session_id"] = new_agent_session_id

            # エージェントの最初の質問を reply に差し込む
            first_q = agent_start.get("question", "")
            count = agent_start.get("candidates_count", "?")
            if first_q:
                reply_text = (
                    f"NeuroSymbolic 該非判定エージェントを起動しました。"
                    f"（候補 {count}件で絞り込み開始）\n\n{first_q}"
                )
            result_actions = [a for a in result_actions if a.get("type") != "start_agent"]
            result_choices = [
                {"label": "わからない",     "message": "よくわかりません。一般的な回答を教えてください"},
                {"label": "該当なし",       "message": "該当しません"},
                {"label": "エージェント終了", "message": "__hantei_cancel__"},
            ]
        except httpx.HTTPError:
            reply_text += "（エージェント起動に失敗しました。後でもう一度お試しください）"

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



# ── モジュール別コーチングプロンプト・テンプレート ────────────────────────────
_COACHING_TEMPLATES: dict[str, dict] = {
    "8001": {
        "name": "AI 該非判定 — 審査フォーカス",
        "prompt_supplement": (
            "このモジュールでは AI 該非判定が主要業務です。以下を重点的にサポートしてください:\n"
            "1. 判定ステータスの解釈: intersection=黄（優先確認）/ core_only=青（リストヒット）/ expanded_only=灰（低リスク）\n"
            "2. スクリーニング実行前に取引先の英語正式法人名を確認するよう促してください\n"
            "3. 判定結果が CONTROLLED の場合は「経産省への許可申請 or 輸出禁止」を案内してください\n"
            "4. NeuroSymbolic 該非判定エージェントを使うと対話形式で外為法・EAR 該当性を確認できることを教えてください\n"
            "5. 案件の作成から CSV 出力までの5ステップを明確に案内してください"
        ),
    },
    "8002": {
        "name": "品目管理 — 用途記述フォーカス",
        "prompt_supplement": (
            "品目管理では「用途概要の記述品質」が AI 判定精度に直結します:\n"
            "1. 用途概要には「工程・装置・性能・最終使用地」の4要素が揃っているか確認してください\n"
            "   不足している要素があれば具体的に補足を促してください\n"
            "2. HS コード判定（port 8006）と連携していることを案内し、判定前に HS コードが正しいか確認するよう促してください\n"
            "3. 品目コードは EAR ECCN or 輸出令項番に対応させてください\n"
            "4. 同一品目を複数の取引に使う場合は品目マスタの再利用を案内してください"
        ),
    },
    "8003": {
        "name": "R&D リスク管理 — プロファイル入力フォーカス",
        "prompt_supplement": (
            "R&D リスク管理では「用途要件」と「需要者要件」プロファイルが最重要です:\n"
            "1. 用途要件の入力補助: 工程（どのような製造・研究プロセスか）/ 装置（使用する機器・設備）/ "
            "性能（スペック・能力値）/ 最終使用地（工場・研究所の国・地域）\n"
            "2. 需要者要件: 法人名（英語正式表記）/ 所在地 / 第三者への技術提供の有無\n"
            "3. AI 審査が完了したら「品目管理へ登録」でシームレスにワークフローを継続できることを案内\n"
            "4. みなし輸出（国内の非居住者への技術提供）も規制対象である点を適宜案内してください"
        ),
    },
    "8004": {
        "name": "特許検索 — 技術調査フォーカス",
        "prompt_supplement": (
            "特許検索モジュールでは先行技術調査・競合特許分析が主目的です:\n"
            "1. 検索クエリは技術用語を英語で入力すると精度が向上します\n"
            "2. 経済安全保障推進法の特許出願非公開制度（2024年施行）: "
            "安全保障上重要な技術は外国出願前に政府審査が必要。特許検索中に該当可能性を発見した場合は法務部門への確認を促す\n"
            "3. 検索結果の「類似度スコア」が 0.8 以上は高関連性とみてください\n"
            "4. 特許出願前に競合特許との重複がないか確認する重要性を伝えてください"
        ),
    },
    "8005": {
        "name": "スクリーニング — 制裁チェックフォーカス",
        "prompt_supplement": (
            "スクリーニングでは制裁リストへの正確なマッチングが重要です:\n"
            "1. 企業名は英語正式法人名で入力（例: Huawei Technologies Co., Ltd.）\n"
            "2. 結果の読み方: match=確定ヒット（取引中止）/ possible_match=確認必要（法務相談）/ no_match=問題なし\n"
            "3. BIS 50%ルール: SDN 指定企業が 50%以上保有する企業も同等の制裁対象\n"
            "4. スクリーニングは取引のたびに実行が必要（リストは随時更新される）\n"
            "5. OFAC SDN・BIS Entity List・METI 外国ユーザーリスト・EU 統合制裁リストの4リストを照合していることを伝える"
        ),
    },
    "8006": {
        "name": "HS コード判定 — 分類精度フォーカス",
        "prompt_supplement": (
            "HS コード判定では正確な品目分類が輸出申告と規制判定の基礎となります:\n"
            "1. 品目説明は具体的な材質・機能・用途を含めると精度が向上します\n"
            "2. 6桁（国際共通）と10桁（日本固有の細分類）の違いを案内してください\n"
            "3. HS コードは輸出令別表第1の項番（外為法規制）とは別体系ですが、"
            "判定の参考情報として AI 該非判定モジュールに連携されます\n"
            "4. 疑義がある場合は税関への事前分類照会を案内してください"
        ),
    },
    "8000": {
        "name": "プラットフォーム — 全体ナビゲーション",
        "prompt_supplement": (
            "プラットフォームのトップ画面では全体フローの案内が主目的です:\n"
            "1. 標準ワークフロー: R&D審査(8003) → 品目管理(8002) → AI該非判定(8001) → スクリーニング(8005)\n"
            "2. 新規案件は R&D リスク管理から始めることを推奨してください\n"
            "3. NeuroSymbolic 該非判定エージェントは AI 該非判定モジュール(8001)の取引詳細から起動できます\n"
            "4. 各モジュールが連携してデータを共有していることを説明してください"
        ),
    },
}


@router.get("/api/chat/coaching-templates")
def get_coaching_templates() -> list:
    """モジュール別コーチングプロンプト・テンプレート一覧を返す"""
    return [
        {
            "port": port,
            "name": tmpl["name"],
            "prompt_supplement": tmpl["prompt_supplement"],
        }
        for port, tmpl in _COACHING_TEMPLATES.items()
    ]


@router.post("/api/chat/app-configs/{port}/apply-template")
def apply_coaching_template(port: str, db: Session = Depends(get_db)) -> dict:
    """指定ポートのデフォルトコーチングテンプレートを適用する"""
    tmpl = _COACHING_TEMPLATES.get(port)
    if not tmpl:
        raise HTTPException(status_code=404, detail=f"port {port} のテンプレートがありません")

    from datetime import datetime as _dt
    cfg = db.query(DapChatConfig).filter(DapChatConfig.port == port).one_or_none()
    if cfg:
        cfg.prompt_supplement = tmpl["prompt_supplement"]
        cfg.enabled = 1
        cfg.updated_at = _dt.utcnow()
    else:
        label = next((m["label"] for m in _CONFIG_PORTS if m["port"] == port), "")
        cfg = DapChatConfig(
            port=port,
            label=label,
            enabled=1,
            prompt_supplement=tmpl["prompt_supplement"],
        )
        db.add(cfg)
    db.commit()
    return {"ok": True, "port": port, "template_name": tmpl["name"]}


@router.post("/api/chat/app-configs/apply-all-templates")
def apply_all_coaching_templates(db: Session = Depends(get_db)) -> dict:
    """全モジュールにデフォルトコーチングテンプレートを一括適用する"""
    from datetime import datetime as _dt
    applied = []
    for port, tmpl in _COACHING_TEMPLATES.items():
        cfg = db.query(DapChatConfig).filter(DapChatConfig.port == port).one_or_none()
        if cfg:
            cfg.prompt_supplement = tmpl["prompt_supplement"]
            cfg.enabled = 1
            cfg.updated_at = _dt.utcnow()
        else:
            label = next((m["label"] for m in _CONFIG_PORTS if m["port"] == port), "")
            cfg = DapChatConfig(
                port=port,
                label=label,
                enabled=1,
                prompt_supplement=tmpl["prompt_supplement"],
            )
            db.add(cfg)
        applied.append(port)
    db.commit()
    return {"ok": True, "applied_ports": applied}


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
