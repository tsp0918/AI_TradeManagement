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

# モジュール別ポータルProxy URL（ナビゲート先はポータル経由に統一）
_PORTAL_MODULE_URLS: dict[str, str] = {
    "ai_validation":    f"{_PLATFORM_URL}/proxy/ai_validation",
    "ai_classification": f"{_PLATFORM_URL}/proxy/ai_classification",
    "rnd_assessment":   f"{_PLATFORM_URL}/proxy/rnd_assessment",
    "patent_search":    f"{_PLATFORM_URL}/proxy/patent_search",
    "screening":        f"{_PLATFORM_URL}/proxy/screening",
    "hs_classifier":    f"{_PLATFORM_URL}/proxy/hs_classifier",
}

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
    "8011": "AI 該非判定（ai_validation）",
    "8002": "品目管理（ai_classification）",
    "8003": "R&D リスク管理（rnd_assessment）",
    "8004": "特許検索（patent_search）",
    "8005": "スクリーニング（screening）",
    "8006": "HS コード判定（hs_classifier）",
    "8010": "DAP 管理画面",
}

# ワークフロー標準順序（前工程 → 後工程）
_WORKFLOW_STAGES: dict[str, str] = {
    "8003": "R&D審査",
    "8002": "品目管理",
    "8011": "AI該非判定",
    "8005": "スクリーニング",
    "8006": "HSコード判定",
    "8004": "特許調査",
    "8000": "プラットフォーム",
}
_WORKFLOW_ORDER = ["8003", "8002", "8011", "8005"]  # 推奨フロー順

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
    actions: list[dict[str, Any]] = []         # [{type, target, value?, ...}]
    choices: list[dict[str, Any]] = []         # [{label, message}]
    intake_state: Optional[dict[str, Any]] = None   # ヒアリング中の場合
    guidance: list[dict[str, Any]] = []        # ステップ別ガイダンス (guided tour)
    alert: Optional[dict[str, Any]] = None     # 自発的アラート {type, message, severity}
    persona_summary: Optional[dict[str, Any]] = None  # ユーザー理解状態


# ── モジュール別デフォルト choices（Claude が省略した場合のフォールバック）──────
_DEFAULT_CHOICES: dict[str, list[dict]] = {
    "8011": [
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


class EventRequest(BaseModel):
    session_id: Optional[str] = None
    event_type: str   # "page_view" | "button_click" | "guide_shown" | "guide_dismissed" | "form_submit"
    context: dict[str, Any] = {}


class GreetRequest(BaseModel):
    session_id: Optional[str] = None
    context: dict[str, Any] = {}

# ── チャットウィジェット設定スキーマ ──────────────────────────────────────
class ChatConfigUpdate(BaseModel):
    enabled: int = 1
    prompt_supplement: str = ""


# ── RAG: 多層 FAISS + オントロジー クエリ ─────────────────────────────────────

_RAG_TRIGGER_TERMS = frozenset([
    "ECCN", "外為法", "輸出令", "別表", "規制", "該非", "キャッチオール",
    "リスト規制", "EAR", "BIS", "みなし輸出", "技術的パラメータ", "数値閾値",
    "控制", "Wassenaar", "デュアルユース", "許可要件", "条件", "仕様",
    "HS", "IPC", "特許", "技術", "核", "半導体", "暗号", "センサー",
])

# ECCN / HS / IPC コードを検出する正規表現
import re as _re
_ECCN_PATTERN = _re.compile(r'\b([0-9][A-E]\d{3}(?:\.\w+)*)\b', _re.IGNORECASE)
_HS_PATTERN   = _re.compile(r'\b(\d{4}[\.\s]?\d{2})\b')
_IPC_PATTERN  = _re.compile(r'\b([A-H]\d{2}[A-Z])\b')

_RAG_MIN_SCORE = 0.60
_RAG_TOP_K = 4
_RAG_PATENT_TOP_K = 3
_RAG_PAPER_TOP_K = 2


async def _rag_layer_a(message: str) -> str:
    """Layer A（規制テキスト）RAG。規制関連語を含む場合に検索して返す。"""
    if not any(t in message for t in _RAG_TRIGGER_TERMS):
        return ""

    url = f"{_PLATFORM_URL}/api/faiss/search/layer-a"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, params={"q": message[:200], "top_k": _RAG_TOP_K})
        if resp.status_code != 200:
            return ""
        data = resp.json()
    except Exception:
        return ""

    hits = data.get("hits", [])
    lines = []
    for h in hits:
        if h.get("score", 0) < _RAG_MIN_SCORE:
            continue
        title = h.get("title") or h.get("item_no") or ""
        text = h.get("full_text", "")[:150]
        src = h.get("source_type", "")
        lines.append(f"・[{src}] {title}: {text}")

    if not lines:
        return ""
    return "【規制条文DB】\n" + "\n".join(lines[:3])


async def _rag_ontology(message: str) -> str:
    """
    メッセージから ECCN / HS / IPC コードを抽出してオントロジーを検索し、
    説明チェーン（分類根拠・外為法対応・エビデンス数）を返す。
    """
    # コード抽出
    eccns = _ECCN_PATTERN.findall(message)
    hs_codes = _HS_PATTERN.findall(message)
    ipc_codes = _IPC_PATTERN.findall(message)

    queries = []
    for e in eccns[:2]:
        queries.append(e.upper())
    for h in hs_codes[:1]:
        queries.append(h.replace(".", "").replace(" ", ""))
    for i in ipc_codes[:1]:
        queries.append(i.upper())

    if not queries:
        return ""

    lines = []
    async with httpx.AsyncClient(timeout=5.0) as client:
        for q in queries:
            try:
                resp = await client.get(
                    f"{_PLATFORM_URL}/api/ontology/lookup",
                    params={"q": q, "top_k": 2},
                )
                if resp.status_code != 200:
                    continue
                d = resp.json()
                chain = d.get("explanation_chain", [])
                if chain:
                    lines.append(f"[{q}]")
                    lines.extend(f"  {c}" for c in chain[:5])
            except Exception:
                continue

    if not lines:
        return ""
    return "【オントロジー分類根拠】\n" + "\n".join(lines)


async def _rag_layer_b(message: str, eccns: list[str] | None = None) -> str:
    """
    Layer B（特許）RAG。ECCN タグまたはメッセージ内容で関連特許を検索。
    技術的根拠・先行技術として提示する。
    """
    if not any(t in message for t in _RAG_TRIGGER_TERMS):
        return ""

    url = f"{_PLATFORM_URL}/api/faiss/search/layer-b"
    q = message[:200]
    if eccns:
        q = f"{' '.join(eccns[:2])} {q}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, params={"q": q, "top_k": _RAG_PATENT_TOP_K})
        if resp.status_code != 200:
            return ""
        data = resp.json()
    except Exception:
        return ""

    hits = data.get("hits", [])
    lines = []
    for h in hits:
        if h.get("score", 0) < _RAG_MIN_SCORE:
            continue
        pub_no = h.get("publication_number", "")
        title  = h.get("title", "")[:60]
        eccn_tags = h.get("eccn_tags", [])[:2]
        tag_str = f" [{','.join(eccn_tags)}]" if eccn_tags else ""
        lines.append(f"・{pub_no}{tag_str}: {title}")

    if not lines:
        return ""
    return "【関連特許DB（技術根拠）】\n" + "\n".join(lines)


async def _rag_layer_d(message: str, eccns: list[str] | None = None) -> str:
    """
    Layer D（学術論文）RAG。技術動向・最新研究を根拠として提示する。
    """
    if not any(t in message for t in _RAG_TRIGGER_TERMS):
        return ""

    url = f"{_PLATFORM_URL}/api/faiss/search/layer-d"
    q = message[:200]
    eccn_filter = ",".join(eccns[:2]) if eccns else None
    params: dict = {"q": q, "top_k": _RAG_PAPER_TOP_K}
    if eccn_filter:
        params["eccn"] = eccn_filter
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, params=params)
        if resp.status_code != 200:
            return ""
        data = resp.json()
    except Exception:
        return ""

    hits = data.get("hits", [])
    lines = []
    for h in hits:
        if h.get("score", 0) < _RAG_MIN_SCORE:
            continue
        title = h.get("title", "")[:60]
        year  = h.get("year", "")
        cites = h.get("citation_count", 0)
        lines.append(f"・{title}（{year}年, 引用{cites}回）")

    if not lines:
        return ""
    return "【関連学術論文DB（技術動向）】\n" + "\n".join(lines)


async def _rag_country_control(message: str, eccns: list[str]) -> str:
    """
    メッセージから仕向国を検出し、ECCN × 仕向国のライセンス要否をRAGコンテキストに注入する。
    """
    if not eccns:
        return ""

    # 国名検出（簡易）
    msg_lower = message.lower()
    detected_country = None
    for name, iso in _COUNTRY_NAME_TO_ISO.items():
        if name.lower() in msg_lower:
            detected_country = iso
            break
    # ISO コード直接検出
    if not detected_country:
        import re as _re
        m = _re.search(r'\b(CN|US|JP|KR|TW|RU|KP|IR|SY|IN|DE|FR|GB|AU)\b', message.upper())
        if m:
            detected_country = m.group(1)

    if not detected_country:
        return ""

    lines = []
    async with httpx.AsyncClient(timeout=5.0) as client:
        for eccn in eccns[:2]:
            try:
                resp = await client.get(
                    f"{_PLATFORM_URL}/api/ontology/license-check",
                    params={"eccn": eccn, "country": detected_country},
                )
                if resp.status_code != 200:
                    continue
                d = resp.json()
                verdict = d.get("verdict", "")
                verdict_label = d.get("verdict_label", "")
                country_name = d.get("country_name_ja", detected_country)
                cg = d.get("country_group", "")
                fefta = d.get("fefta_zone", "")
                lines.append(
                    f"・ECCN {eccn} × {country_name}({detected_country}): "
                    f"{verdict_label} [Group {cg} / {fefta}]"
                )
            except Exception:
                continue

    if not lines:
        return ""
    return "【Country Control ライセンス要否】\n" + "\n".join(lines)


async def _rag_multilayer(message: str) -> str:
    """
    多層 RAG: Layer A（規制）+ オントロジー + Country Control + Layer B（特許）+ Layer D（論文）を
    並列に検索し、統合されたコンテキストブロックを返す。
    """
    # 先にECCNコードを抽出してLayer B/D/Country Control検索に使う
    eccns_found = [e.upper() for e in _ECCN_PATTERN.findall(message)]

    # 並列実行
    import asyncio
    layer_a_task    = asyncio.create_task(_rag_layer_a(message))
    ontology_task   = asyncio.create_task(_rag_ontology(message))
    country_task    = asyncio.create_task(_rag_country_control(message, eccns_found))
    layer_b_task    = asyncio.create_task(_rag_layer_b(message, eccns_found))
    layer_d_task    = asyncio.create_task(_rag_layer_d(message, eccns_found))

    results = await asyncio.gather(
        layer_a_task, ontology_task, country_task, layer_b_task, layer_d_task,
        return_exceptions=True,
    )

    blocks = [r for r in results if isinstance(r, str) and r.strip()]
    if not blocks:
        return ""
    return "\n\n".join(blocks)


# ── User Persona Tracking ─────────────────────────────────────────────────────
_EXPERT_TERMS = frozenset([
    "ECCN", "外為法", "EAR", "みなし輸出", "キャッチオール", "BIS", "OFAC",
    "Wassenaar", "Country Chart", "項番", "大量破壊兵器", "リスト規制",
    "FEFTA", "輸出令", "安全保障貿易管理", "デュアルユース", "SDN",
    "Entity List", "再輸出規制", "AT管理", "NS管理", "CISAC", "CISTEC",
])
_NOVICE_SIGNALS = [
    "とは何", "って何", "意味が", "よくわかりません", "初めて", "はじめて",
    "どうすればいい", "どうやって", "どういう", "なんですか", "教えてください",
]


def _init_persona() -> dict:
    return {
        "business_level":     "unknown",  # unknown | novice | intermediate | expert
        "module_familiarity": {},         # {port: visit_count}
        "knowledge_gaps":     [],         # 専門用語で質問されたもの
        "workflow_goal":      None,       # 高レベルの目標
        "interaction_count":  0,
    }


def _update_persona(persona: dict, message: str, ctx: dict) -> dict:
    """メッセージとコンテキストからペルソナを更新する（差分 dict を返す）"""
    updates: dict = {}
    updates["interaction_count"] = persona.get("interaction_count", 0) + 1

    # モジュール親しみ度
    port = str(ctx.get("port", ""))
    if port:
        fam = dict(persona.get("module_familiarity", {}))
        fam[port] = fam.get(port, 0) + 1
        updates["module_familiarity"] = fam

    # 専門知識レベル推定
    current_level = persona.get("business_level", "unknown")
    expert_score = sum(1 for t in _EXPERT_TERMS if t in message)
    novice_score  = sum(1 for s in _NOVICE_SIGNALS if s in message)

    if expert_score >= 3 and current_level in ("unknown", "novice", "intermediate"):
        updates["business_level"] = "expert"
    elif expert_score >= 1 and current_level in ("unknown", "novice"):
        updates["business_level"] = "intermediate"
    elif novice_score >= 2 and current_level == "unknown":
        updates["business_level"] = "novice"

    # ギャップ検出（専門用語 + 質問パターン）
    if any(s in message for s in ["とは", "って何", "わからない", "意味"]):
        gaps = list(persona.get("knowledge_gaps", []))
        for term in _EXPERT_TERMS:
            if term in message and term not in gaps:
                gaps.append(term)
        if gaps != persona.get("knowledge_gaps", []):
            updates["knowledge_gaps"] = gaps

    return updates


def _persona_context_str(persona: dict, session_data: dict) -> str:
    """システムプロンプトに埋め込むペルソナ要約文を生成する"""
    level = persona.get("business_level", "unknown")
    level_desc = {
        "unknown":      "習熟度不明 — まず把握しながら丁寧に対応。専門用語には括弧で説明を添える",
        "novice":       "初心者 — 専門用語を避け業務フローを基礎から説明。「なぜやるか」を都度説明する",
        "intermediate": "中級者 — 基本フローは理解済み。規制詳細・判定根拠の説明を厚くする",
        "expert":       "上級者 — 専門用語OK。冗長な説明は省き詳細・根拠・例外に踏み込む",
    }
    fam  = persona.get("module_familiarity", {})
    used = [f"{_MODULE_MAP.get(p, p)}(×{c})" for p, c in fam.items() if c > 0]
    gaps = persona.get("knowledge_gaps", [])
    intake = session_data.get("intake_state")

    lines = [
        f"ユーザー習熟度: {level_desc.get(level, level)}",
        f"使用済みモジュール: {', '.join(used) or '（初回）'}",
    ]
    if gaps:
        lines.append(f"補足が必要な用語: {', '.join(gaps[:5])}")
    if intake and not intake.get("completed"):
        p = intake.get("product_name") or "未確認"
        c = intake.get("destination_country") or "未確認"
        lines.append(f"進行中ヒアリング: 品目={p} / 仕向国={c} / ターン{intake.get('turn_count', 0)}")
    return "\n".join(lines)


# ── Workflow State Analysis ───────────────────────────────────────────────────

def _analyze_workflow_state(session_data: dict, ctx: dict) -> dict:
    """
    ワークフロー全体の状態を分析して自発的アラートとギャップを返す。
    Returns: {stage, gap_modules, proactive_alerts}
    """
    port    = str(ctx.get("port", ""))
    persona = session_data.get("persona") or {}
    fam     = persona.get("module_familiarity", {})
    intake  = session_data.get("intake_state")
    stage   = _WORKFLOW_STAGES.get(port, "その他")
    gap_modules: list[str] = []
    alerts:      list[dict] = []

    # 前工程で未使用のモジュールを検出（最初の1件だけ警告）
    if port in _WORKFLOW_ORDER:
        idx = _WORKFLOW_ORDER.index(port)
        for prev in _WORKFLOW_ORDER[:idx]:
            if fam.get(prev, 0) == 0:
                prev_name = _WORKFLOW_STAGES.get(prev, prev)
                alerts.append({
                    "type":     "workflow_gap",
                    "severity": "warn",
                    "guide_id": f"workflow_gap:{prev}:{port}",
                    "message":  (
                        f"通常は「{prev_name}」を先に完了してから"
                        f"「{stage}」に進みます。"
                        f"前工程から始めますか？"
                    ),
                    "action_hint": f"「{prev_name}から始めたい」と話しかけると案内します",
                })
                gap_modules.append(prev)
                break

    # AI判定にいるがスクリーニング未実施
    if port == "8011" and fam.get("8005", 0) == 0:
        alerts.append({
            "type":     "missing_step",
            "severity": "warn",
            "guide_id": f"screening_missing:{port}",
            "message":  (
                "取引先スクリーニング（制裁リストチェック）がまだ実施されていません。"
                "AI判定と並行してスクリーニング（port 8005）を実行することを推奨します。"
            ),
            "action_hint": "「スクリーニングをしたい」と話しかけると手順を案内します",
        })

    # ヒアリング完了済みだが案件未作成
    if intake and intake.get("completed") and not intake.get("created_transaction_id"):
        alerts.append({
            "type":     "pending_action",
            "severity": "info",
            "guide_id": "pending_action:no_tx",
            "message":  (
                "ヒアリング情報が収集済みですが案件がまだ作成されていません。"
                "「案件を作成してください」と話しかけると自動入力します。"
            ),
            "action_hint": "「案件を作成してください」",
        })

    # リスク国仕向けでスクリーニング未実施
    if intake:
        dest = (intake.get("destination_country") or "").upper()
        if dest[:2] in {"CN", "RU", "KP", "IR", "BY", "SY", "CU"} and fam.get("8005", 0) == 0:
            alerts.append({
                "type":     "risk_warning",
                "severity": "danger",
                "guide_id": f"risk_country:{dest[:2]}",
                "message":  (
                    f"仕向国「{intake.get('destination_country')}」は重点管理対象国です。"
                    "取引先スクリーニングを優先的に実施してください。"
                ),
                "action_hint": "「スクリーニングを実行」と話しかけると手順を案内します",
            })

    # 規制動向モニタリング — 未読の warn/danger 変更をアラート化
    try:
        import httpx as _httpx
        r = _httpx.get(f"{_PLATFORM_URL}/api/regulatory/changes?limit=3", timeout=2)
        if r.status_code == 200:
            reg_changes = r.json().get("changes", [])
            for rc in reg_changes:
                sev = rc.get("severity", "info")
                if sev not in ("warn", "danger"):
                    continue
                guide_id = f"reg_change:{rc.get('id')}"
                alerts.append({
                    "type":        "regulatory_update",
                    "severity":    sev,
                    "guide_id":    guide_id,
                    "message":     rc.get("title", "規制動向に変更があります"),
                    "action_hint": "「規制変更の詳細を教えて」と話しかけると内容を案内します",
                })
    except Exception:
        pass  # platform-core 未起動時は無視

    # 審査が止まっている案件の検出 (ai_validation API 経由)
    _ai_val_url = os.environ.get("MODULE_AI_VALIDATION_URL", "http://localhost:8011")
    try:
        import httpx as _httpx
        r = _httpx.get(f"{_ai_val_url}/api/transactions/stuck", timeout=3)
        if r.status_code == 200:
            stuck = r.json().get("stuck_transactions", [])
            if stuck:
                names = "、".join(
                    f"#{t['id']}({t.get('title','') or t.get('case_no','')})" for t in stuck[:3]
                )
                alerts.append({
                    "type":        "stuck_review",
                    "severity":    "warn",
                    "guide_id":    f"stuck_review:{','.join(str(t['id']) for t in stuck[:3])}",
                    "message":     f"審査が止まっている案件が {len(stuck)} 件あります: {names}。続きを進めましょうか？",
                    "action_hint": f"「案件{stuck[0]['id']}の審査を続けたい」と話しかけてください",
                })
    except Exception:
        pass  # ai_validation 未起動時は無視

    # 輸出許可証の期限・残高アラート (platform-core API 経由)
    try:
        import httpx as _httpx
        r = _httpx.get(f"{_PLATFORM_URL}/api/export-licenses?status=approved&limit=20", timeout=2)
        if r.status_code == 200:
            from datetime import datetime as _dt, timezone as _tz
            licenses = r.json() if isinstance(r.json(), list) else r.json().get("licenses", [])
            now = _dt.now(_tz.utc)
            for lic in licenses[:20]:
                # 期限アラート（30日以内）
                exp = lic.get("expires_at")
                if exp:
                    try:
                        exp_dt = _dt.fromisoformat(exp.replace("Z", "+00:00"))
                        days_left = (exp_dt - now).days
                        if 0 <= days_left <= 30:
                            alerts.append({
                                "type":        "license_expiry",
                                "severity":    "danger" if days_left <= 7 else "warn",
                                "guide_id":    f"license_expiry:{lic.get('id')}",
                                "message":     f"許可証「{lic.get('application_number','?')}」の期限まで{days_left}日です。再申請を検討してください（UC8）",
                                "action_hint": "「輸出許可申請を確認したい」と話しかけてください",
                            })
                    except Exception:
                        pass
                # 残高アラート（20%以下）
                total = lic.get("value_usd") or 0
                remaining = lic.get("license_value_remaining_usd")
                if total and remaining is not None and total > 0:
                    ratio = remaining / total
                    if ratio <= 0.2:
                        alerts.append({
                            "type":        "license_balance",
                            "severity":    "warn",
                            "guide_id":    f"license_balance:{lic.get('id')}",
                            "message":     f"許可証「{lic.get('application_number','?')}」の残存許可額が{ratio*100:.0f}%以下です（残：${remaining:,.0f}）。出荷継続には再申請が必要かもしれません",
                            "action_hint": "「輸出許可証の残高を確認したい」と話しかけてください",
                        })
    except Exception:
        pass  # platform-core 未起動時は無視

    return {"stage": stage, "gap_modules": gap_modules, "proactive_alerts": alerts}


# ── ヒアリングモード: トリガーキーワード ──────────────────────────────────────
_INTAKE_TRIGGERS = [
    "新規案件", "案件を登録", "輸出案件", "ヒアリング", "相談",
    "案件を作", "登録したい", "輸出したい", "どこから始め",
    "何から始め", "どうすればいい", "初めて", "はじめて",
]

def _is_intake_trigger(message: str) -> bool:
    """ヒアリングモード開始トリガーかどうかを判定する"""
    return any(kw in message for kw in _INTAKE_TRIGGERS)


# ── ヒアリングモード: セッション状態初期化 ─────────────────────────────────────
def _init_intake_state() -> dict:
    return {
        "stage": "situation",    # situation → product → destination → enduser → confirm
        "product_name":         None,
        "product_description":  None,
        "declared_usage":       None,
        "destination_country":  None,
        "end_user":             None,
        "known_eccn":           None,
        "transaction_type":     None,  # "export" | "deemed_export"
        "risk_flags":           [],    # 検出されたリスク（懸念国、曖昧な用途等）
        "gaps":                 [],    # 未解決の不明事項
        "turn_count":           0,
    }


# ── ヒアリングモード: システムプロンプト ─────────────────────────────────────
def _build_intake_system_prompt(intake: dict) -> str:
    # 収集済みフィールドのサマリー
    filled: list[str] = []
    if intake.get("product_name"):
        filled.append(f"品目: {intake['product_name']}")
    if intake.get("product_description"):
        filled.append(f"仕様: {intake['product_description'][:80]}...")
    if intake.get("declared_usage"):
        filled.append(f"用途: {intake['declared_usage'][:80]}...")
    if intake.get("destination_country"):
        filled.append(f"仕向国: {intake['destination_country']}")
    if intake.get("end_user"):
        filled.append(f"需要者: {intake['end_user']}")
    if intake.get("known_eccn"):
        filled.append(f"ECCN/外為法項: {intake['known_eccn']}")

    filled_block = "\n".join(f"  ✓ {f}" for f in filled) if filled else "  （まだヒアリング開始前）"

    gaps = intake.get("gaps", [])
    gaps_block = "\n".join(f"  ❓ {g}" for g in gaps) if gaps else "  （なし）"

    risk_flags = intake.get("risk_flags", [])
    risk_block = "\n".join(f"  ⚠️ {r}" for r in risk_flags) if risk_flags else "  （なし）"

    return f"""あなたは輸出管理コンプライアンス部門の先輩担当者です。
後輩（ユーザー）の輸出案件を対話形式でヒアリングしています。

【あなたの役割と姿勢】
- 後輩が「何を・どこへ・誰に・何のために」輸出するかを正確に把握する
- 後輩が気づいていないリスクや確認事項を発見し、理解を促進する
- 曖昧な回答には必ず掘り下げを行う（「研究用途」→「どんな研究？どの工程で使う？」）
- 5〜8ターンで情報収集を完了し、案件概要を確認してからシステムアクションに移る
- ユーザーが「わからない」と言う項目はギャップとして記録し、後で確認事項として残す

【収集すべき情報（必須）】
1. 品目名・型番・技術仕様（何を輸出するか）
2. 申告用途（工程/装置/性能/最終使用地の4要素で具体的に）
3. 仕向国（どこへ）
4. 需要者（誰に：法人名・所在地・第三者提供の有無）

【リスクサインの検出と対応】
- 仕向国が CN/RU/KP/IR/BY の場合：キャッチオール規制・EAR Country Chartについて確認
- 用途が「研究」「一般」「評価」のみ：具体的な工程・装置・目的を引き出す
- 需要者が「不明」「顧客」：法人名と最終使用場所の確認を要求する
- みなし輸出の可能性（国内の外国人研究者への技術提供）：居住年数・国籍を確認
- ECCN が 3x5xx 系・半導体製造装置・精密加工：米国 2022年10月規制の可能性を指摘

【ヒアリング完了条件】
上記4項目が揃い、リスクサインに対する追加確認が完了したとき。
完了時は is_intake_complete=true を返し、収集した情報をまとめて action_plan を提示する。

【action_plan の構成（完了時）】
実行予定のシステムアクションをリストで提示し、ユーザーの確認を求める。例:
  「では以下を実行します。よろしいですか？
   1. AI該非判定に案件を新規作成（品目・用途・取引先を自動入力）
   2. 取引先スクリーニングを実行（OFAC/BIS/METI照合）
   3. AI判定パイプラインを起動（外為法マトリクス照合）」

【現在のヒアリング状況】
収集済み情報:
{filled_block}

未解決のギャップ:
{gaps_block}

検出されたリスクフラグ:
{risk_block}

ターン数: {intake.get('turn_count', 0)} / 8（目標完了ターン数）

【ルール】
- reply: 口語体日本語。150字以内。先輩が後輩に話すような自然なトーン。
- 必ず1つの核心的な質問か確認で終わる（複数質問を一度に投げない）
- choices は次の回答候補を2〜3件提示（ユーザーが選びやすいように）
- 最終的な法令解釈は専門家確認を推奨する旨を適宜追加"""


# ── ヒアリングモード: respond_intake ツール ────────────────────────────────────
_RESPOND_INTAKE_TOOL = {
    "name": "respond_intake",
    "description": (
        "ヒアリング中の返答を構造化フォーマットで返す。"
        "今ターンで収集した情報を intake_updates に記録し、"
        "ヒアリング完了時は is_intake_complete=true を設定する。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reply": {
                "type": "string",
                "description": "ユーザーへの返答。口語体日本語。150字以内。必ず質問か確認で終わる。",
            },
            "intake_updates": {
                "type": "object",
                "description": "今ターンで収集・確定した情報フィールド。未収集は含めない。",
                "properties": {
                    "product_name":        {"type": "string"},
                    "product_description": {"type": "string"},
                    "declared_usage":      {"type": "string"},
                    "destination_country": {"type": "string"},
                    "end_user":            {"type": "string"},
                    "known_eccn":          {"type": "string"},
                    "transaction_type":    {"type": "string", "enum": ["export", "deemed_export"]},
                },
            },
            "risk_flags_new": {
                "type": "array",
                "description": "今ターンで新たに検出されたリスクフラグ（すでに記録済みのものは含めない）",
                "items": {"type": "string"},
            },
            "gaps_new": {
                "type": "array",
                "description": "今ターンで判明した未解決の不明事項",
                "items": {"type": "string"},
            },
            "is_intake_complete": {
                "type": "boolean",
                "description": "必須4項目が揃い追加リスク確認も完了した場合に true。",
            },
            "action_plan": {
                "type": "array",
                "description": "is_intake_complete=true の場合のみ。実行予定のアクション一覧。",
                "items": {
                    "type": "object",
                    "properties": {
                        "step":        {"type": "integer", "description": "順序番号（1から）"},
                        "label":       {"type": "string",  "description": "ユーザー向け説明（20字以内）"},
                        "action_type": {"type": "string",
                                        "enum": ["create_transaction", "run_screening",
                                                 "run_ai_validation", "start_agent",
                                                 "navigate_to", "manual"],
                                        "description": "実行するアクション種別"},
                        "params":      {"type": "object", "description": "アクション実行パラメータ"},
                    },
                    "required": ["step", "label", "action_type"],
                },
            },
            "choices": {
                "type": "array",
                "description": "次の回答候補。2〜3件。",
                "minItems": 2,
                "items": {
                    "type": "object",
                    "properties": {
                        "label":   {"type": "string"},
                        "message": {"type": "string"},
                    },
                    "required": ["label", "message"],
                },
            },
        },
        "required": ["reply", "choices"],
    },
}


# ── ヒアリング完了後: アクション実行 ─────────────────────────────────────────
async def _execute_action_plan(
    action_plan: list[dict],
    intake: dict,
    platform_url: str,
    ai_validation_url: str,
) -> list[dict]:
    """
    確認済みの action_plan を実行し、実行結果サマリーを返す。

    戻り値: [{step, label, result, detail}]
    """
    results: list[dict] = []

    for step in action_plan:
        action_type = step.get("action_type")
        label = step.get("label", "")
        params = step.get("params", {})

        try:
            if action_type == "create_transaction":
                result = await _create_transaction(intake, ai_validation_url)
                results.append({"step": step["step"], "label": label,
                                 "result": "ok", "detail": f"案件ID: {result.get('id')}"})
                # 後続ステップのために transaction_id をセット
                intake["created_transaction_id"] = result.get("id")

            elif action_type == "run_screening":
                end_user = intake.get("end_user") or params.get("company_name", "")
                result = await _run_screening(end_user, platform_url)
                status = result.get("result_status", "unknown")
                results.append({"step": step["step"], "label": label,
                                 "result": "ok", "detail": f"スクリーニング: {status}"})

            elif action_type == "run_ai_validation":
                tx_id = intake.get("created_transaction_id") or params.get("transaction_id")
                if tx_id:
                    result = await _run_pipeline(tx_id, ai_validation_url)
                    results.append({"step": step["step"], "label": label,
                                     "result": "ok", "detail": "パイプライン起動"})
                else:
                    results.append({"step": step["step"], "label": label,
                                     "result": "skip", "detail": "案件IDなし"})

            elif action_type == "navigate_to":
                url = params.get("url", "")
                results.append({"step": step["step"], "label": label,
                                 "result": "ok", "detail": f"移動先: {url}"})

            else:
                results.append({"step": step["step"], "label": label,
                                 "result": "manual", "detail": "手動で実行してください"})

        except Exception as e:
            results.append({"step": step["step"], "label": label,
                             "result": "error", "detail": str(e)[:100]})

    return results


async def _create_transaction(intake: dict, ai_validation_url: str) -> dict:
    """ai_validation に新規トランザクションを作成する"""
    payload = {
        "title": f"{intake.get('product_name', '品目名未定')} 輸出審査",
        "counterparty_name": intake.get("end_user") or "",
        "destination_country": intake.get("destination_country") or "",
        "items": [
            {
                "item_name": intake.get("product_name") or "",
                "item_description": intake.get("product_description") or "",
            }
        ],
        "usage_requirements": [
            {
                "source": "core",
                "text": intake.get("declared_usage") or "",
            }
        ] if intake.get("declared_usage") else [],
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{ai_validation_url}/api/transactions",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


async def _run_screening(company_name: str, platform_url: str) -> dict:
    """screening モジュールで企業スクリーニングを実行する"""
    screening_url = os.environ.get("MODULE_SCREENING_URL", "http://localhost:8005")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{screening_url}/api/screen",
            json={"company_name": company_name},
        )
        resp.raise_for_status()
        return resp.json()


async def _run_pipeline(transaction_id: int, ai_validation_url: str) -> dict:
    """ai_validation パイプラインを起動する"""
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{ai_validation_url}/decision/{transaction_id}/run-and-two-lists",
        )
        resp.raise_for_status()
        return resp.json()


# ── System prompt 構築 ───────────────────────────────────────────────
def _build_system_prompt(ctx: dict[str, Any], prompt_supplement: str = "") -> str:
    port         = str(ctx.get("port", ""))
    module_name  = _MODULE_MAP.get(port, "不明なモジュール")
    page_path    = ctx.get("page_path", "（不明）")
    current_task = ctx.get("current_task", "")

    # ペルソナ・ワークフロー状態（_persona/_workflow は chat() が ctx に注入）
    persona_ctx  = ctx.get("_persona_str", "")
    workflow_ctx = ctx.get("_workflow_alerts", "")

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

    # RAG context from multilayer FAISS + ontology (injected by chat() when regulation terms detected)
    rag_block = ctx.get("_rag_context", "")
    rag_section = (
        f"\n\n【規制・技術知識ベース（参照情報）】\n"
        f"以下は関連する規制条文・分類根拠・特許・論文の要約です。回答の根拠として活用してください:\n"
        f"{rag_block}"
    ) if rag_block else ""

    return f"""あなたは輸出管理コンプライアンス業務の AI アシスタントです。
ユーザーが今この画面で何をしようとしているかを理解し、業務フローの文脈に沿って行動まで完結させてください。

【業務フロー全体像】
このシステムは以下のモジュール連鎖で構成されています:

1. R&D リスク管理（{_PORTAL_MODULE_URLS['rnd_assessment']}）
   研究開発案件のリスク審査・プロジェクト登録
   フロー: 案件作成 → プロファイル入力（用途要件・需要者要件）→ AI審査 → 品目管理へワンクリック登録
   入力必須: 用途要件（工程/装置/性能/最終使用地）、需要者要件（法人名/場所/第三者提供の有無）

2. 品目管理（{_PORTAL_MODULE_URLS['ai_classification']}）
   輸出品目の登録・分類・AI判定依頼
   フロー: 品目登録（品目コード・名称・仕様）→ 用途概要入力 → HSコード判定（8006連携）→ AI該非判定を依頼
   注意: 用途概要が不十分だと AI 判定の精度が落ちる。工程/装置/性能/最終使用地の4要素を含めること

3. AI 該非判定（{_PORTAL_MODULE_URLS['ai_validation']}）
   外為法マトリクス照合・案件審査
   フロー: 案件作成 → スクリーニング実行（screening連携）→ AI判定実行 → 結果確認 → CSV/PDF出力
   結果の読み方: intersection=要注意(黄)、core_only=直接リストヒット(青)、expanded_only=低リスク(灰)

4. スクリーニング（{_PORTAL_MODULE_URLS['screening']}）
   取引先の制裁リストチェック
   注意: 企業名は英語正式法人名で入力するとマッチ精度が上がる

5. 特許検索（{_PORTAL_MODULE_URLS['patent_search']}）: 技術的先行技術・競合特許の調査
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

■ キャッチオール規制 判定フロー（6ステップ・決定論的）
・Step 1: エンブレムト国チェック — 北朝鮮(KP)/イラン(IR)/ロシア(RU)等 E:1 → 即座に REQUIRES_PERMIT
・Step 2: ホワイト国チェック — A:1〜A:6 グループ国（米欧豪等 42カ国） → CLEAR
・Step 3: EAR Country Chart 照合 — 13列（NS1/NS2/MT/NP1/NP2/CB1〜CB3/CW1/CW2/AT1/AT2/UN）× ECCN別エントリ
・Step 4: Red Flag 7項目チェック — 不審な用途/支払/迂回経路/技術水準不一致等
・Step 5: スコアリング（0〜4: リスクレベル算定）
・Step 6: REQUIRES_PERMIT（許可必要）/ REVIEW（要精査）/ CLEAR（懸念なし）
・キャッチオール詳細は「get_catchall_detail」ツールで取得可能（transaction_id があれば呼び出せる）

■ BOM管理・De Minimis規則（FDP Rule / EAR §734.9）
・De Minimis規則: 外国製品に組み込まれた米国原産規制品目の価値が製品全体の一定割合を超える場合、EARが適用される
  - 一般規制対象国向け: 25%超でEAR適用（国際販売価格ベースで計算）
  - テロ支援国向け（KP/IR/SY/CU/BY）: 10%超でEAR適用
  - 米国外で開発・生産された品目でも米国技術が含まれれば対象
・FDPR（外国直接製品ルール, §736.2(b)(3)）:
  - 米国内で生産・開発されたソフトウェア・技術を「直接利用」して製造した外国製品は、ECCNに関わらずEAR管理下
  - 2022年ロシア・ベラルーシFDPR、2022年10月中国FDPRで適用範囲が大幅拡大
  - 半導体製造装置・GAA FET・NAND 128層超・DRAM 18nm未満は新FDPR対象
・BOM確認ポイント: 品目コード毎に「原産国」「米国技術コンテンツ比率」「対象ECCN」を記録すること
  → サプライヤーポータル（UC7）でサプライヤーへの申告書発行→書類確認→BOMツリー確認の流れを使う

■ 輸出許可証管理（ライセンス管理 / UC8）
・許可証の種類:
  - 個別許可証（STA: Strategic Trade Authorization, NLR: No License Required, EAR99等）
  - 一般許可証（EAR: License Exception）
  - 外為法: 包括許可（ホワイト国向け）/ 個別許可（REQUIRES_PERMIT案件）
・ライセンスの有効条件4要素（変更不可）: ①品目・ECCN ②仕向地 ③需要者（End User） ④用途（End Use）
  → 4要素のいずれか変更がある場合は再申請が必要
・バリューベース許可証: 出荷のたびに許可額を控除する（残存許可額の管理が義務）
  - 残存許可額20%以下でアラート → 再申請（リードタイム: BIS 2〜3ヶ月、METI 2〜4週間）
  - 有効期限30日以内で警告 → 期限延長申請または再申請を検討
・出荷実績記録義務: インボイス・パッキングリスト・B/L・許可証番号・出荷日（外為法7年、EAR5年保存）
・Re-Export（再輸出）: 米国管理品目を第三国から別の国へ再輸出する場合も許可が必要な場合がある

■ 海外品目マスター管理（グローバル品目管理 / UC3）
・国別規制プロファイルの必要性:
  - 同一品目でも仕向地によってECCN・HS・適用規制が異なる（日米欧で別体系）
  - EUではEU Dual-Use Regulation 2021/821に基づく独自ECCNが必要（例: EU ML/1C001など）
  - ローカルECCN（local_eccn）と輸出許可要否（license_required）を品目×国別に管理すること
・FTA特恵税率の実務ポイント:
  - 原産地規則（ROO: Rules of Origin）を満たさないと特恵税率適用不可
  - 品目別原産地規則（PSR）: HS桁数変更（CTH/CTS）または付加価値基準（VA）で判定
  - 累積条項: CPTPP・RCEPでは協定相手国間の製造工程を累積計算できる
  - 自己申告制度（CPTPP・RCEP）: 輸出者・生産者が原産地証明書を自ら作成可能
・グローバル規制レジームのクロスチェック:
  品目がWassenaar ML・EU Dual-Use・ITAR/USMLに同時該当する場合、最も厳しい規制が適用
  → 「グローバル規制レジーム照合」（/api/regulatory/regime-check）でワンストップ確認

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

【ユーザー理解】
{persona_ctx if persona_ctx else "（セッション開始直後 — 慎重にレベルを把握しながら対応する）"}

【ワークフロー状況】
{workflow_ctx if workflow_ctx else "（問題なし）"}{rag_section}

【行動指針】
- ユーザーの発言から「最終的にやりたいこと」を推測し、そのゴールへの最短経路を案内する
- 現在のページにない機能が必要な場合は、必ず actions に {{"type": "navigate_to", "url": "<ポータルURL>"}} を含める。reply にURLを書くだけでは不十分（ユーザーが手動で移動できない）
- navigate_to の url は必ず http://localhost:8000/proxy/<モジュールキー>/ 形式のポータルURL を使う。http://localhost:80XX などの直接ポートURLは絶対に使わない
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
  reply: AI審査が完了していれば「品目管理へ登録」ボタンでワンクリック登録できます。登録後は品目管理モジュールで品目の詳細を確認できます。
  actions: [{{"type": "highlight", "target": "品目管理へ登録"}}]
  choices: [{{"label": "品目管理に移動", "message": "品目管理（8002）でやること一覧を教えてください"}}, {{"label": "登録後の流れ", "message": "品目管理登録後にAI該非判定を依頼するにはどうしますか"}}]

例5: DAP画面、ユーザー「スクリーニングしたい」、interactive_elements に該当なし
  reply: スクリーニングはスクリーニングモジュールで実行できます。移動しますか？
  actions: [{{"type": "navigate_to", "url": "http://localhost:8000/proxy/screening/"}}]
  choices: [{{"label": "移動する", "message": "スクリーニングモジュールに移動してください"}}, {{"label": "スクリーニングとは", "message": "スクリーニングの手順を教えてください"}}]

例6: DAP画面、ユーザー「AI分類画面に行きたい」
  reply: AI分類モジュールに移動します。
  actions: [{{"type": "navigate_to", "url": "http://localhost:8000/proxy/ai_classification/"}}]
  choices: [{{"label": "AI分類の手順", "message": "AI分類の手順を教えてください"}}, {{"label": "戻り方", "message": "DAPに戻るにはどうしますか"}}]

【NeuroSymbolic 該非判定エージェント（重要機能）】
- ユーザーが以下のような意図を示した場合、actions に {{"type": "start_agent", ...}} を含める:
  「該非判定したい」「規制対象か確認したい」「輸出できるか確認したい」「外為法に引っかかるか」
  「NeuroSymbolicエージェント」「対話形式で判定」「AI質問形式で判定」など
- initial_query にはユーザーが述べた品目・仕様・取引に関する情報をそのまま渡す（日本語可）
- transaction_id はコンテキストから判断できる場合のみ数値で設定（不明な場合は省略）
- start_agent を含む場合の reply は「これから〇〇について確認していきます」という自然な誘導文にする
- エージェントが起動すると、以降の返答はシステムが生成し、ユーザーは自由に日本語・英語で回答できる
- ユーザーが明示的な専門用語（"civilian"等）を知らなくても構わない。システムが自動的に解釈する

【ユースケース別ナビゲーションガイド — 業務完結まで伴走する】

ユーザーが「〇〇したい」「〇〇のやり方を教えて」と言ったとき、以下の対応するUCとステップを案内する。
navigate_to と highlight を組み合わせ、次にすべき操作を1ステップずつ誘導すること。

UC1: 新規品目の輸出審査（品目登録 → HS判定 → 制裁照合 → AI該非判定）
  Step1: 「業務フロー → 品目管理 → 新規登録」で品目名・型番・仕様を入力
    navigate_to: http://localhost:8000/proxy/ai_classification/products
  Step2: 品目詳細の「HSコード判定」ボタンで AI が上位5候補を提示 → 選択して「反映」
    highlight: HSコード判定
  Step3: 「業務フロー → 取引先スクリーニング → 一括照合」で取引先名を制裁照合
    navigate_to: http://localhost:8000/proxy/screening/ui/batch
  Step4: 「業務フロー → AI該非判定 → 新規審査」で取引を作成し判定実行
    navigate_to: http://localhost:8000/proxy/ai_validation/ui/transactions/new

UC2: R&D起案→品目登録（プロジェクト起案 → AIリスク評価 → 知財審査 → 品目登録）
  Step1: 「業務フロー → R&Dリスク評価 → 新規プロジェクト」でプロジェクト作成
    navigate_to: http://localhost:8000/proxy/rnd_assessment/ui/cases/new
  Step2: 3テンプレート（使用用途・需要者・開示）を入力してスコアリング確認
    highlight: プロファイル入力
  Step3: Explainabilityで開示戦略・セキュリティポスチャを確認（80点以上は即エスカレーション）
  Step4: 知財審査フォームに審査結果・エビデンスをアップロード
    highlight: 知財審査
  Step5: 「品目管理への登録」ボタンで品目管理に連携
    highlight: 品目管理への登録

UC3: 海外品目マスター管理・国別規制プロファイル（品目確認 → 国別設定 → FTA確認 → レジーム照合）
  Step1: 「品目管理」で対象品目のHS/ECCN・仕様を確認
    navigate_to: http://localhost:8000/proxy/ai_classification/products
  Step2: 品目詳細の「国別タブ」で仕向地国ごとにローカルECCN・輸出許可要否を設定
    highlight: 国別規制プロファイル
  Step3: 「EPA/FTA特恵税率チェック」でHSコード×仕向地で最優遇税率を照会
    navigate_to: http://localhost:8000/ui/fta-check
  Step4: グローバル規制レジーム照合（ITAR/USML・EU Dual-Use・Wassenaar・NSG）をAI該非判定から確認
    navigate_to: http://localhost:8000/proxy/ai_validation/ui/transactions
  Step5: サプライヤーポータルで原産地証明・De Minimis比率を確認
    navigate_to: http://localhost:8000/ui/supply-chain

UC7: BOM管理・De Minimis算出（申告作成 → BOMツリー → De Minimis算出 → FDPR判定）
  Step1: 「サプライチェーン管理」でサプライヤー申告を作成してポータルURLをサプライヤーへ送付
    navigate_to: http://localhost:8000/ui/supply-chain
  Step2: 提出書類受領後にBOMツリーで品目ごとの原産国・価値比率を確認
    highlight: BOMツリー
  Step3: De Minimis比率を確認（一般規制対象国: 25%超でEAR適用、テロ支援国: 10%閾値）
    highlight: De Minimis
  Step4: FDPR（外国直接製品ルール）フラグをAI該非判定案件のサプライチェーンリンクに保存
    navigate_to: http://localhost:8000/proxy/ai_validation/ui/transactions

UC8: 出荷ライセンス管理（許可証確認 → 条件一致確認 → 価値控除 → 証跡保存）
  Step1: 「輸出許可申請」で該当ライセンスの残存許可額・有効期限を確認
    navigate_to: http://localhost:8000/ui/export-license
  Step2: 出荷品目・仕向地・需要者・用途が許可証条件と一致しているか4要素確認
    highlight: 条件確認
  Step3: 出荷のたびに「価値控除」フォームで出荷額を入力し残存許可額を更新
    highlight: 価値控除
  Step4: インボイス・B/L番号を案件詳細に添付して証跡保存（外為法7年・EAR5年保存）
    navigate_to: http://localhost:8000/proxy/ai_validation/ui/transactions
  Step5: 残存許可額20%以下・期限30日以内のアラートをダッシュボードで確認して再申請判断
    navigate_to: http://localhost:8000/dashboard

UC4: 取引先デューデリジェンス（制裁照合 → 与信スコア → 進捗記録）
  Step1: 「業務フロー → 取引先スクリーニング → 一括照合」で7ソース照合
    navigate_to: http://localhost:8000/proxy/screening/ui/batch
  Step2: 「専門ツール → 与信管理」で財務・統合スコアを確認
    navigate_to: http://localhost:8000/ui/counterparty
  Step3: 「受注後ERP → コンプライアンス進捗」に結果を記録
    navigate_to: http://localhost:8000/ui/compliance-lookup

UC5: みなし輸出審査（人物スクリーニング → 申請 → 経済安保チェック → 台帳記録）
  Step1: 「R&Dリスク評価 → 人物管理」で外国籍研究者を登録・スクリーニング
    navigate_to: http://localhost:8000/proxy/rnd_assessment/ui/personnel
  Step2: 「業務フロー → 役務取引管理 → みなし輸出申請」で申請を作成
    navigate_to: http://localhost:8000/proxy/ai_validation/ui/services
  Step3: 「専門ツール → 経済安保法チェック」でICP自己診断8要素を確認
    navigate_to: http://localhost:8000/proxy/rnd_assessment/ui/icp-diagnosis

UC6: 輸出許可申請（REQUIRES_PERMIT取引 → ドラフト生成 → 価値消費管理）
  Step1: AI該非判定で REQUIRES_PERMIT と判定された取引詳細の「輸出許可申請ドラフト生成」ボタン
    highlight: 輸出許可申請ドラフト生成
  Step2: 「受注後ERP → 輸出許可申請」で申請を管理（申請番号自動採番）
    navigate_to: http://localhost:8000/ui/export-license
  Step3: 出荷のたびに「価値控除」フォームで残存許可額を更新
    highlight: 価値控除

UC9: 技術インテリジェンス調査（特許検索 → 制裁照合 → 学術論文 → R&Dリンク）
  Step1: 「専門ツール → 特許検索」でキーワード・CPC分類で検索
    navigate_to: http://localhost:8000/proxy/patent_search/
  Step2: 検索結果の発明者・出願人に「制裁照合」ボタンで照合
    highlight: 制裁照合
  Step3: 「R&Dリスク評価 → 技術インテリジェンス」で学術論文類似度を確認
    navigate_to: http://localhost:8000/proxy/rnd_assessment/ui/academic-intel

【よくある質問と回答（DAP即答ガイド）】

Q: CLEAR/REVIEW/REQUIRES_PERMITの意味は？
A: CLEAR=規制リストに明確な一致なし（最終判断は担当者が行う）。REVIEW=部分一致または要注意フラグあり（追加確認が必要）。REQUIRES_PERMIT=輸出許可申請が必要（UC6の手順で申請書を作成してください）

Q: スクリーニング結果がMATCHした場合は？
A: 取引を即時停止し、法務・コンプライアンス担当者にエスカレーションしてください。一致エンティティの詳細（ソース・エイリアス）を証跡として保存してください。自己判断で取引を進めてはいけません。

Q: EAR99の品目は審査不要？
A: 不要ではありません。制裁対象国・制裁対象者への輸出は禁止されています。キャッチオール規制により大量破壊兵器への転用が疑われる場合は EAR99 でも許可が必要です。必ず制裁スクリーニングとRed Flagチェックを実施してください。

Q: みなし輸出とは？
A: 外国籍の研究者・技術者が社内で規制技術にアクセスすることが「輸出」とみなされる制度（外為法2022年改正）。社内の研究室内であっても対象になります。事前審査が必要です。

Q: ECCN番号の調べ方は？
A: まず品目管理のHSコード判定でHSコードを特定し、HS→ECCNマッピングから候補を取得してください。それでも不明な場合は米国メーカーへのCCATS申請または経済産業省への相談が必要です。

Q: De Minimis 25%超えはどうすれば？
A: 一般的な規制対象国向けは25%超でEARが外国製品にも適用されます（FDPルール）。AI該非判定で再審査し、REQUIRES_PERMITなら輸出許可申請（UC6）が必要です。テロ支援国（KP/IR/SY等）は10%が閾値です。

Q: 審査記録の保存期間は？
A: 外為法では7年間（施行令）、米国EARでは5年間（15 CFR §762）の記録保持が必要です。本プラットフォームの審査結果はすべてデータベースに保存されています。

Q: De Minimis 25%はどうやって計算する？
A: 「米国原産の規制品目の価値 ÷ 製品全体の販売価格 × 100」で算出します。価値は製品に使用された米国品目コストを積み上げます。計算結果はサプライチェーン管理のBOMツリーでDe Minimis比率として確認できます（UC7）。テロ支援国向けは10%閾値なので注意してください。

Q: 輸出許可証の残高が不足しそうな場合は？
A: 残存許可額が20%以下または期限まで30日以内になるとダッシュボードでアラートが表示されます。BIS申請は2〜3ヶ月かかるため、出荷スケジュールから逆算して早めに再申請が必要です。輸出許可申請管理（UC8）のStep5で確認・再申請の判断ができます。

Q: 同じ品目でも国によって規制が違う？
A: はい。ECCNは米国EARの分類で仕向地に関係なく一定ですが、仕向地によって必要な許可証が変わります（EAR Country Chart）。品目管理の国別規制プロファイル（UC3）でローカルECCN・輸出許可要否を国別に管理することで、仕向地ごとの対応を一元管理できます。

Q: FDPRとは何ですか？
A: 外国直接製品ルール（Foreign Direct Product Rule）です。外国で製造された製品でも、米国のソフトウェアや技術を使って製造・設計された場合はEARが適用されます。2022年以降ロシア・中国向けは特別FDPRが適用されており、半導体・AI・先端技術製品は特に注意が必要です。BOM管理（UC7）のStep4でFDPRフラグを確認してください。

Q: 海外拠点から輸出する場合の注意点は？
A: 海外拠点（例：欧州子会社）からの輸出であっても、日本親会社から技術・ソフトウェアの提供がある場合は外為法の「役務取引」として審査が必要です。また、EU拠点ではEU Dual-Use規則2021/821への対応が別途必要です。品目の国別規制プロファイル（UC3）でローカルECCNを設定し、各規制体系での要件を確認してください。

Q: BOM管理でサプライヤーから情報が取れない場合は？
A: サプライヤーポータルのURL発行機能（UC7 Step1）でサプライヤーに申告書提出を依頼できます。提出期限を設定して催促することも重要です。情報が取得できない場合は「最悪ケース仮定」（全て規制対象と仮定）でDe Minimisを計算し、リスクが高ければ代替部品の調達を検討してください。

【NeuroSymbolic 該非判定エージェント（重要機能）】
- ユーザーが以下のような意図を示した場合、actions に {{"type": "start_agent", ...}} を含める:
  「該非判定したい」「規制対象か確認したい」「輸出できるか確認したい」「外為法に引っかかるか」
  「NeuroSymbolicエージェント」「対話形式で判定」「AI質問形式で判定」など
- initial_query にはユーザーが述べた品目・仕様・取引に関する情報をそのまま渡す（日本語可）
- transaction_id はコンテキストから判断できる場合のみ数値で設定（不明な場合は省略）
- start_agent を含む場合の reply は「これから〇〇について確認していきます」という自然な誘導文にする
- エージェントが起動すると、以降の返答はシステムが生成し、ユーザーは自由に日本語・英語で回答できる
- ユーザーが明示的な専門用語（"civilian"等）を知らなくても構わない。システムが自動的に解釈する

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


# 国名 → ISO 2文字コード 簡易辞書
_COUNTRY_NAME_TO_ISO: dict[str, str] = {
    "日本": "JP", "japan": "JP", "アメリカ": "US", "米国": "US", "アメリカ合衆国": "US",
    "united states": "US", "usa": "US", "us": "US", "中国": "CN", "china": "CN",
    "韓国": "KR", "south korea": "KR", "korea": "KR", "台湾": "TW", "taiwan": "TW",
    "ドイツ": "DE", "germany": "DE", "フランス": "FR", "france": "FR",
    "イギリス": "GB", "英国": "GB", "uk": "GB", "united kingdom": "GB",
    "ロシア": "RU", "russia": "RU", "北朝鮮": "KP", "north korea": "KP",
    "イラン": "IR", "iran": "IR", "シリア": "SY", "syria": "SY",
    "インド": "IN", "india": "IN", "シンガポール": "SG", "singapore": "SG",
    "タイ": "TH", "thailand": "TH", "インドネシア": "ID", "indonesia": "ID",
    "マレーシア": "MY", "malaysia": "MY", "ベトナム": "VN", "vietnam": "VN",
    "オーストラリア": "AU", "australia": "AU", "カナダ": "CA", "canada": "CA",
    "UAE": "AE", "アラブ首長国連邦": "AE", "サウジアラビア": "SA", "saudi arabia": "SA",
    "パキスタン": "PK", "pakistan": "PK", "ベラルーシ": "BY", "belarus": "BY",
}


async def _extract_attr_value(
    user_message: str,
    missing_attr: dict,
    client: "anthropic.Anthropic",
) -> str:
    """
    ユーザーの自然言語回答から属性値を抽出し、エージェントAPIが受け取れる形式に正規化する。
    抽出失敗時は "unknown" を返す。
    """
    attr_key = missing_attr.get("attr_key", "")
    label = missing_attr.get("label", "")
    unit = missing_attr.get("unit", "")
    example = missing_attr.get("example", "")

    # 国名コード変換（ローカル辞書で先に試みる）
    if attr_key == "destination_country":
        msg_lower = user_message.strip().lower()
        # 2文字大文字コードが直接入力された場合
        import re as _re
        iso_match = _re.search(r'\b([A-Z]{2})\b', user_message.upper())
        for key, iso in _COUNTRY_NAME_TO_ISO.items():
            if key.lower() in msg_lower:
                return iso
        if iso_match:
            return iso_match.group(1)

    # 数値属性: 単純なパース
    if unit:
        import re as _re
        nums = _re.findall(r'[\d]+\.?[\d]*', user_message.replace(",", ""))
        if nums:
            return nums[0]

    # LLM による抽出（Haiku - 低コスト）
    # 属性ごとの有効値ヒント
    _valid_hints: dict[str, str] = {
        "end_use_type": "civilian(民生用), military(軍事用), weapons(武器), dual_use_military(デュアルユース), research(研究), government(政府), unknown(不明)",
        "end_user_type": "commercial(民間企業), military_org(軍事組織), government(政府機関), research_inst(研究機関), individual(個人), unknown(不明)",
        "substance_type": "cw_precursor(化学兵器前駆体), bw_agent(生物剤), cw_agent(化学剤), industrial_chemical(産業用化学品), pharmaceutical(医薬品)",
    }
    valid_hint = _valid_hints.get(attr_key, "")

    extraction_prompt = f"""以下のユーザー回答から「{label}」の値を抽出してください。

属性キー: {attr_key}
{f'有効な値: {valid_hint}' if valid_hint else ''}
{f'単位: {unit}' if unit else ''}
{f'例: {example}' if example else ''}

ユーザー回答:
{user_message}

【指示】
- 上記の有効な値のいずれかに最もよく対応するものを1つだけ返してください
- 数値の場合は数字のみ（単位なし）を返してください
- 判断できない場合は "unknown" を返してください
- 理由や説明は不要です。値のみを返してください"""

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=50,
            messages=[{"role": "user", "content": extraction_prompt}],
        )
        extracted = resp.content[0].text.strip().split("\n")[0].strip()
        # 空や長すぎる場合はフォールバック
        if not extracted or len(extracted) > 50:
            return "unknown"
        return extracted
    except Exception:
        return user_message.strip()  # 抽出失敗時はそのまま渡す


def _format_agent_turn(agent_resp: dict, initial_query: str = "") -> tuple[str, list[dict]]:
    """
    agent API レスポンスをチャット用 reply + choices に自然な形式で変換する。
    戻り値: (reply, choices)
    """
    if agent_resp.get("is_ready_for_judgment"):
        reply = (
            "確認に必要な情報が揃いました。\n\n"
            "収集した情報をもとに該非判定を実行します。"
            "「判定を実行」ボタンを押すか、追加で確認したいことがあればお知らせください。"
        )
        choices = [
            {"label": "判定を実行",       "message": "__hantei_execute_judge__"},
            {"label": "判定前に確認したい", "message": "判定前にもう少し詳しく教えてください"},
            {"label": "やり直す",          "message": "__hantei_cancel__"},
        ]
    else:
        question = agent_resp.get("question", "")
        missing_attr = agent_resp.get("missing_attr") or {}
        label = missing_attr.get("label", "")

        if not question:
            question = "もう少し詳しく教えていただけますか？"

        reply = question

        # コンテキストに応じた選択肢
        attr_key = missing_attr.get("attr_key", "")
        if attr_key == "end_use_type":
            choices = [
                {"label": "民生・商業用途",  "message": "民間の商業目的での使用です"},
                {"label": "研究・開発用途",  "message": "研究開発目的での使用です"},
                {"label": "用途が不明",      "message": "具体的な用途はまだ決まっていません"},
                {"label": "やめる",          "message": "__hantei_cancel__"},
            ]
        elif attr_key == "end_user_type":
            choices = [
                {"label": "民間企業",    "message": "民間企業が最終需要者です"},
                {"label": "研究機関",    "message": "大学・研究機関が最終需要者です"},
                {"label": "政府・官公庁", "message": "政府機関が最終需要者です"},
                {"label": "不明",        "message": "最終需要者はまだ確定していません"},
                {"label": "やめる",      "message": "__hantei_cancel__"},
            ]
        elif attr_key == "destination_country":
            choices = [
                {"label": "日本国内",      "message": "日本国内向けです"},
                {"label": "米国",          "message": "アメリカ合衆国向けです"},
                {"label": "EU域内",        "message": "EU加盟国向けです"},
                {"label": "中国",          "message": "中国向けです"},
                {"label": "仕向地不明",    "message": "仕向地はまだ決まっていません"},
                {"label": "やめる",        "message": "__hantei_cancel__"},
            ]
        else:
            choices = [
                {"label": "わからない・確認中", "message": "この点についてはまだ確認できていません"},
                {"label": "やめる",            "message": "__hantei_cancel__"},
            ]
    return reply, choices


def _format_judge_result(judge_data: dict) -> tuple[str, list[dict]]:
    """判定結果を自然言語でフォーマットする。戻り値: (reply, choices)"""
    status = judge_data.get("overall_status", "pending")
    summary = judge_data.get("summary", "")
    controlled = judge_data.get("controlled_items", [])
    excluded = judge_data.get("excluded_items", [])
    reasons = judge_data.get("reasons", [])

    # ステータスラベル
    status_labels = {
        "CLEAR":          "✅ 規制対象外（該当なし）",
        "REVIEW":         "⚠️ 要確認（一部項目について追加調査が必要）",
        "REQUIRES_PERMIT": "🔴 許可申請必要",
        "CONTROLLED":     "🔴 規制品目に該当",
        "pending":        "⏳ 判定保留中",
    }
    status_label = status_labels.get(status, f"判定結果: {status}")

    lines = [f"**該非判定が完了しました。**", "", f"**総合判定: {status_label}**", ""]

    if controlled:
        # FEFTA/ECCN IDを読みやすく変換
        controlled_readable = []
        for item in controlled:
            if item.startswith("FEFTA::"):
                parts = item.split("::")
                item_no = parts[1] if len(parts) > 1 else item
                controlled_readable.append(f"外為法 輸出令別表第1 第{item_no}項")
            elif item.startswith("ECCN::"):
                eccn = item.replace("ECCN::", "")
                controlled_readable.append(f"EAR ECCN {eccn}")
            else:
                controlled_readable.append(item)
        lines.append(f"**規制対象項目:** {' / '.join(controlled_readable)}")
        lines.append("")

    if excluded:
        lines.append(f"該当なし（除外）: {len(excluded)}件")
        lines.append("")

    if summary:
        lines.append(summary)
        lines.append("")

    if status in ("REQUIRES_PERMIT", "CONTROLLED"):
        lines.append("輸出許可証の申請または担当部門への確認が必要です。")
    elif status == "REVIEW":
        lines.append("一部項目について追加情報の収集または専門家への確認をお勧めします。")

    choices = [
        {"label": "判定結果の詳細を確認",   "message": "判定結果の詳細と根拠を詳しく説明してください"},
        {"label": "許可申請の手続きを確認", "message": "輸出許可申請はどのように進めればよいですか"},
        {"label": "別の品目を判定する",     "message": "新しい品目で該非判定エージェントを起動したい"},
    ]
    return "\n".join(lines), choices


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

    # ── ペルソナ更新 ──────────────────────────────────────────────────────
    persona = session_data.get("persona") or _init_persona()
    persona_updates = _update_persona(persona, req.message, req.context)
    persona = {**persona, **persona_updates}
    if req.session_id:
        session_data["persona"] = persona

    # ── NeuroSymbolic エージェントモード ────────────────────────────────────
    agent_session_id: Optional[str] = session_data.get("hantei_agent_session_id")
    if agent_session_id:
        message = req.message.strip()

        # キャンセル
        if message == "__hantei_cancel__":
            session_data["hantei_agent_session_id"] = None
            session_data.pop("hantei_missing_attr", None)
            if req.session_id:
                _save_session(req.session_id, session_data)
            return ChatResponse(
                reply="該非判定を終了しました。いつでも再開できます。何か他にお手伝いできることがあればお気軽にどうぞ。",
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
                session_data.pop("hantei_missing_attr", None)
                if req.session_id:
                    _save_session(req.session_id, session_data)
                reply, choices = _format_judge_result(judge_data)
                return ChatResponse(reply=reply, actions=[], choices=choices)

            # 自然言語 → 属性値 抽出（前回のmissing_attrを使用）
            missing_attr = session_data.get("hantei_missing_attr") or {}
            if missing_attr and missing_attr.get("attr_key"):
                normalized = await _extract_attr_value(message, missing_attr, client)
            else:
                normalized = message

            # 正規化した値をエージェントAPIに送信
            agent_resp = await _call_agent_answer(agent_session_id, normalized)

            # 次回の抽出のために missing_attr を保存
            if agent_resp.get("missing_attr"):
                session_data["hantei_missing_attr"] = agent_resp["missing_attr"]
            else:
                session_data.pop("hantei_missing_attr", None)

            reply, choices = _format_agent_turn(agent_resp)

            if req.session_id:
                history_buf = session_data.get("history", [])
                history_buf.append({"role": "user", "content": message})
                history_buf.append({"role": "assistant", "content": reply})
                session_data["history"] = history_buf[-_SESSION_MAX_HISTORY:]
                _save_session(req.session_id, session_data)

            return ChatResponse(reply=reply, actions=[], choices=choices)

        except httpx.HTTPError as e:
            session_data["hantei_agent_session_id"] = None
            session_data.pop("hantei_missing_attr", None)
            if req.session_id:
                _save_session(req.session_id, session_data)
            return ChatResponse(
                reply="一時的に接続できませんでした。もう一度お試しいただくか、しばらく経ってから再開してください。",
                actions=[],
                choices=[
                    {"label": "再起動",         "message": "該非判定エージェントをもう一度起動したい"},
                    {"label": "通常操作を続ける", "message": "次にすることを教えてください"},
                ],
            )
    # ── エージェントモードここまで ───────────────────────────────────────────

    # ── ヒアリングモード ──────────────────────────────────────────────────────
    intake_state: Optional[dict] = session_data.get("intake_state")

    # 確認待ちモード: ユーザーが action_plan に「はい」「実行」と回答した場合
    if intake_state and intake_state.get("awaiting_confirm"):
        message = req.message.strip()
        confirmed = any(kw in message for kw in [
            "はい", "よろしく", "お願い", "実行", "進め", "OK", "ok", "確認", "承認"
        ])
        cancelled = any(kw in message for kw in ["いいえ", "キャンセル", "やめ", "待って", "修正"])

        if confirmed:
            action_plan = intake_state.get("pending_action_plan", [])
            ai_val_url = os.environ.get("MODULE_AI_VALIDATION_URL", "http://localhost:8011")
            exec_results = await _execute_action_plan(action_plan, intake_state, _PLATFORM_URL, ai_val_url)
            # 結果サマリーを生成
            ok_steps = [r for r in exec_results if r["result"] in ("ok", "manual")]
            err_steps = [r for r in exec_results if r["result"] == "error"]
            reply = "実行しました。"
            for r in exec_results:
                icon = "✓" if r["result"] == "ok" else ("⚠" if r["result"] == "manual" else "✗")
                reply += f" {icon}{r['label']}({r['detail']})"
            if err_steps:
                reply += f" エラー{len(err_steps)}件あり。"
            # ヒアリングモード終了
            intake_state["awaiting_confirm"] = False
            intake_state["completed"] = True
            intake_state["exec_results"] = exec_results
            if req.session_id:
                session_data["intake_state"] = intake_state
                _save_session(req.session_id, session_data)
            tx_id = intake_state.get("created_transaction_id")
            choices = [
                {"label": "判定結果を確認", "message": "AI判定の結果を見せてください"},
                {"label": "新規案件ヒアリング", "message": "別の案件のヒアリングを始めてください"},
            ]
            if tx_id:
                choices.insert(0, {"label": f"案件{tx_id}を開く",
                                    "message": f"案件{tx_id}の詳細を確認したい"})
            return ChatResponse(
                reply=reply[:300],
                actions=[{"type": "navigate_to", "target": "", "url": f"{_PORTAL_MODULE_URLS['ai_validation']}/ui/transactions/{tx_id}"}] if tx_id else [],
                choices=choices[:3],
                intake_state=intake_state,
            )

        elif cancelled:
            intake_state["awaiting_confirm"] = False
            intake_state["pending_action_plan"] = []
            if req.session_id:
                session_data["intake_state"] = intake_state
                _save_session(req.session_id, session_data)
            return ChatResponse(
                reply="わかりました。何を修正しますか？確認したい項目を教えてください。",
                actions=[],
                choices=[
                    {"label": "品目を修正", "message": "品目の情報を修正したいです"},
                    {"label": "用途を修正", "message": "申告用途を修正したいです"},
                    {"label": "最初からやり直し", "message": "ヒアリングを最初からやり直したい"},
                ],
                intake_state=intake_state,
            )
        # 不明確な回答 → 通常ヒアリングとして続行

    # ヒアリングモード開始チェック（既存ヒアリング中 or 新規トリガー）
    is_in_intake = intake_state is not None and not intake_state.get("completed", False)
    is_new_intake = not is_in_intake and _is_intake_trigger(req.message)

    if is_new_intake and req.session_id:
        intake_state = _init_intake_state()
        session_data["intake_state"] = intake_state
        is_in_intake = True

    if is_in_intake and intake_state:
        intake_state["turn_count"] = intake_state.get("turn_count", 0) + 1

        # ヒアリング用 Claude 呼び出し
        history = session_data.get("history") or req.history
        history = list(history)[-_SESSION_MAX_HISTORY:]
        messages = [m for m in history if m.get("role") in {"user", "assistant"}]
        messages.append({"role": "user", "content": req.message})

        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1200,
                system=_build_intake_system_prompt(intake_state),
                messages=messages,
                tools=[_RESPOND_INTAKE_TOOL],
                tool_choice={"type": "tool", "name": "respond_intake"},
            )
        except anthropic.APIError as e:
            raise HTTPException(status_code=502, detail=f"Claude API エラー: {e}")

        reply_text = ""
        result_choices: list = []
        action_plan: list = []
        is_complete = False

        for block in resp.content:
            if block.type == "tool_use" and block.name == "respond_intake":
                inp = block.input or {}
                reply_text = inp.get("reply", "")
                result_choices = inp.get("choices") or []
                is_complete = inp.get("is_intake_complete", False)
                action_plan = inp.get("action_plan") or []

                # intake_state を更新
                updates = inp.get("intake_updates") or {}
                for field, val in updates.items():
                    if val:
                        intake_state[field] = val
                for flag in (inp.get("risk_flags_new") or []):
                    if flag not in intake_state["risk_flags"]:
                        intake_state["risk_flags"].append(flag)
                for gap in (inp.get("gaps_new") or []):
                    if gap not in intake_state["gaps"]:
                        intake_state["gaps"].append(gap)
                break

        if not reply_text:
            reply_text = next((b.text for b in resp.content if hasattr(b, "text")), "少々お待ちください。")

        if len(result_choices) < 2:
            result_choices = [
                {"label": "わかりました", "message": "はい、わかりました"},
                {"label": "詳しく教えて", "message": "もう少し詳しく教えてください"},
                {"label": "わからない", "message": "この項目はよくわかりません"},
            ]

        # ヒアリング完了 → 確認待ちに移行
        if is_complete and action_plan:
            intake_state["awaiting_confirm"] = True
            intake_state["pending_action_plan"] = action_plan
            result_choices = [
                {"label": "はい、実行する", "message": "はい、実行してください"},
                {"label": "修正したい",     "message": "内容を修正したいです"},
                {"label": "キャンセル",     "message": "キャンセルします"},
            ]

        if req.session_id:
            history.append({"role": "user",      "content": req.message})
            history.append({"role": "assistant",  "content": reply_text})
            session_data["history"] = history[-_SESSION_MAX_HISTORY:]
            session_data["intake_state"] = intake_state
            _save_session(req.session_id, session_data)

        return ChatResponse(
            reply=reply_text,
            actions=[],
            choices=result_choices[:3],
            intake_state={
                "stage":               intake_state.get("stage"),
                "turn_count":          intake_state.get("turn_count"),
                "awaiting_confirm":    intake_state.get("awaiting_confirm", False),
                "is_complete":         is_complete,
                "action_plan":         action_plan,
                "product_name":        intake_state.get("product_name"),
                "destination_country": intake_state.get("destination_country"),
                "risk_flags":          intake_state.get("risk_flags", []),
                "gaps":                intake_state.get("gaps", []),
            },
        )
    # ── ヒアリングモードここまで ─────────────────────────────────────────────

    history = session_data.get("history") or req.history
    history = list(history)[-_SESSION_MAX_HISTORY:]

    # current_task + ペルソナ + ワークフロー状態をコンテキストに注入
    ctx = dict(req.context)
    ctx["current_task"] = session_data.get("task", "")
    port = str(ctx.get("port", ""))

    # ワークフロー分析
    workflow = _analyze_workflow_state(session_data, ctx)
    workflow_alerts = workflow.get("proactive_alerts", [])
    workflow_alert_str = "\n".join(
        f"⚠️ [{a['severity'].upper()}] {a['message']}" for a in workflow_alerts
    ) if workflow_alerts else ""

    ctx["_persona_str"]    = _persona_context_str(persona, session_data)
    ctx["_workflow_alerts"] = workflow_alert_str

    # RAG: 多層検索（Layer A 規制条文 + オントロジー + Layer B 特許 + Layer D 論文）
    rag_context = await _rag_multilayer(req.message)
    if rag_context:
        ctx["_rag_context"] = rag_context

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
                "guidance_steps": {
                    "type": "array",
                    "description": (
                        "ユーザーが複数ステップの操作（他モジュール移動を含む）を必要とする場合のみ生成。"
                        "各ステップはフロントエンドが順番に実行する。通常の質問回答では不要。"
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "step":    {"type": "integer", "description": "順序番号（1から）"},
                            "type":    {
                                "type": "string",
                                "enum": ["navigate", "highlight", "fill_hint", "explain", "watch"],
                                "description": (
                                    "navigate=URLに移動, highlight=要素をハイライト+ツールチップ表示, "
                                    "fill_hint=入力ヒント表示, explain=説明テキスト, watch=次の操作を待つ"
                                ),
                            },
                            "target":  {"type": "string", "description": "対象UI要素のテキスト（highlight/fill_hint/watch）"},
                            "url":     {"type": "string", "description": "navigate 時の移動先URL"},
                            "message": {"type": "string", "description": "ユーザーへの説明文（全type共通）"},
                            "tooltip": {"type": "string", "description": "highlight 時に要素近傍に表示するツールチップ"},
                            "hint":    {"type": "string", "description": "fill_hint 時に入力欄に表示する補助テキスト"},
                            "example": {"type": "string", "description": "fill_hint 時の入力例"},
                        },
                        "required": ["step", "type", "message"],
                    },
                },
                "proactive_alert": {
                    "type": "object",
                    "description": "ユーザーが気づいていないリスクや重要な欠落を自発的に警告する場合のみ設定。",
                    "properties": {
                        "type":     {"type": "string", "enum": ["risk_warning", "workflow_gap", "info"]},
                        "message":  {"type": "string", "description": "警告内容（50字以内）"},
                        "severity": {"type": "string", "enum": ["danger", "warn", "info"]},
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

    result_guidance: list = []
    result_alert: Optional[dict] = None

    for block in resp.content:
        if block.type == "tool_use" and block.name == "respond":
            inp = block.input or {}
            reply_text      = inp.get("reply", "")
            result_actions  = inp.get("actions") or []
            result_choices  = inp.get("choices") or []
            result_guidance = inp.get("guidance_steps") or []
            result_alert    = inp.get("proactive_alert")
            if len(result_choices) < 2:
                result_choices = default_choices
            break

    # ワークフロー分析からのアラートをフォールバックとして使用
    if not result_alert and workflow_alerts:
        top_alert = workflow_alerts[0]
        result_alert = {
            "type":     top_alert.get("type", "info"),
            "message":  top_alert.get("message", ""),
            "severity": top_alert.get("severity", "info"),
        }

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

            # 最初の missing_attr を保存（次の回答抽出に使用）
            if agent_start.get("missing_attr"):
                session_data["hantei_missing_attr"] = agent_start["missing_attr"]

            # エージェントの最初の質問を自然な形で提示
            first_q = agent_start.get("question", "")
            if first_q:
                reply_text = (
                    "該非判定を開始します。いくつか確認させてください。\n\n"
                    f"{first_q}\n\n"
                    "日本語でも英語でも、自由に回答していただいて構いません。"
                )
            result_actions = [a for a in result_actions if a.get("type") != "start_agent"]
            _, result_choices = _format_agent_turn(agent_start)
        except httpx.HTTPError:
            reply_text += "\n\n（判定エージェントの起動に失敗しました。しばらく経ってから再度お試しください）"

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
        guidance=result_guidance,
        alert=result_alert,
        persona_summary={
            "business_level":     persona.get("business_level", "unknown"),
            "workflow_stage":     _WORKFLOW_STAGES.get(port, ""),
            "interaction_count":  persona.get("interaction_count", 0),
            "knowledge_gaps":     persona.get("knowledge_gaps", [])[:3],
            "module_count":       len([v for v in persona.get("module_familiarity", {}).values() if v > 0]),
        },
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


@router.post("/api/chat/greet", response_model=ChatResponse)
async def proactive_greet(req: GreetRequest, db: Session = Depends(get_db)) -> ChatResponse:
    """
    ページロード時に呼び出し、先輩コレーグとして自発的にガイダンスを提供する。
    会話履歴・セッション状態・ペルソナを分析して先回り案内を返す。
    空 reply = 案内不要（UIは何も表示しない）。
    """
    session_data: dict = {}
    if req.session_id:
        session_data = _get_session(req.session_id)

    persona = session_data.get("persona") or _init_persona()

    # モジュール訪問カウント更新
    port = str(req.context.get("port", ""))
    persona_updates = _update_persona(persona, "", req.context)
    persona = {**persona, **persona_updates}
    if req.session_id:
        session_data["persona"] = persona
        _save_session(req.session_id, session_data)

    # ワークフロー分析
    workflow = _analyze_workflow_state(session_data, req.context)
    alerts   = workflow.get("proactive_alerts", [])

    # 表示済みガイドをフィルタリング（同一セッションで同じアラートを繰り返さない）
    shown_guides: set = set(session_data.get("shown_guides") or [])
    alerts = [a for a in alerts if a.get("guide_id") not in shown_guides]

    intake   = session_data.get("intake_state")

    # 初回訪問（このモジュールの訪問数が1 = 今まさにカウントした）
    module_visit = persona.get("module_familiarity", {}).get(port, 1)
    has_history  = len(session_data.get("history", [])) > 0

    # ── ケース別の自発的案内を生成 ──────────────────────────────────────
    # ケース1: アラートあり → 最も重要度の高いものを案内
    if alerts:
        top = max(alerts, key=lambda a: {"danger": 3, "warn": 2, "info": 1}.get(a.get("severity", "info"), 1))

        # 表示済みとしてセッションに記録
        guide_id = top.get("guide_id")
        if guide_id:
            shown_guides.add(guide_id)
            session_data["shown_guides"] = list(shown_guides)
            if req.session_id:
                _save_session(req.session_id, session_data)

        choices = [
            {"label": "詳しく教えて", "message": f"{top['message'][:30]}について詳しく教えてください"},
            {"label": "後で確認する", "message": "わかりました"},
            {"label": "次のステップ", "message": "次に何をすればいいですか"},
        ]
        if top.get("action_hint"):
            choices[0]["message"] = top["action_hint"].strip("「」")
        return ChatResponse(
            reply=top["message"],
            actions=[],
            choices=choices,
            alert={"type": top.get("type", "info"), "message": top["message"], "severity": top.get("severity", "info")},
            persona_summary={
                "business_level":    persona.get("business_level", "unknown"),
                "workflow_stage":    _WORKFLOW_STAGES.get(port, ""),
                "interaction_count": persona.get("interaction_count", 0),
                "knowledge_gaps":    persona.get("knowledge_gaps", [])[:3],
                "module_count":      len([v for v in persona.get("module_familiarity", {}).values() if v > 0]),
            },
        )

    # ケース2: 初回訪問 + 過去のセッション履歴あり → モジュール案内
    if module_visit == 1 and has_history:
        module_name = _MODULE_MAP.get(port, "このモジュール")
        gap_modules = workflow.get("gap_modules", [])
        if gap_modules:
            prev_name = _WORKFLOW_STAGES.get(gap_modules[0], gap_modules[0])
            reply = (
                f"{module_name}へようこそ。"
                f"通常は「{prev_name}」を先に完了してから進みます。"
                f"前工程からやり直しますか？"
            )
            choices = [
                {"label": f"{prev_name}へ",    "message": f"{prev_name}の手順を教えてください"},
                {"label": "このまま続ける", "message": f"{module_name}での作業を進めたいです"},
                {"label": "全体フローを確認",   "message": "全体のワークフローを教えてください"},
            ]
            return ChatResponse(reply=reply, actions=[], choices=choices)

    # ケース3: ヒアリング中のセッションが残っている → 再開を促す
    if intake and not intake.get("completed") and intake.get("turn_count", 0) > 0:
        product = intake.get("product_name") or "（品目未確認）"
        reply = f"前回のヒアリング（品目: {product}）の続きから再開できます。続けますか？"
        choices = [
            {"label": "続きから始める",   "message": "前回のヒアリングを続けてください"},
            {"label": "最初からやり直す", "message": "ヒアリングを最初からやり直したいです"},
            {"label": "今は不要",        "message": "ヒアリングは今は必要ありません"},
        ]
        return ChatResponse(reply=reply, actions=[], choices=choices)

    # 案内不要
    return ChatResponse(reply="", actions=[], choices=[])


@router.post("/api/chat/event")
async def track_event(req: EventRequest) -> dict:
    """
    行動イベントをセッションに記録する（ペルソナ更新・ガイド既読管理）。
    DAP チャットウィジェットが暗黙的に呼び出す。返り値は {ok: true} のみ。
    """
    if not req.session_id:
        return {"ok": True}

    session_data = _get_session(req.session_id)
    ctx = req.context
    event_type = req.event_type

    # ページ訪問記録
    if event_type == "page_view":
        visits = list(session_data.get("page_visits") or [])
        visits.append({
            "port":  str(ctx.get("port", "")),
            "page":  ctx.get("page_path", ""),
        })
        session_data["page_visits"] = visits[-20:]

        # ペルソナ: モジュール訪問カウント更新
        persona = session_data.get("persona") or _init_persona()
        port = str(ctx.get("port", ""))
        if port:
            fam = dict(persona.get("module_familiarity", {}))
            fam[port] = fam.get(port, 0) + 1
            persona["module_familiarity"] = fam
        session_data["persona"] = persona

    # ガイド表示記録（以降同じガイドを返さない）
    elif event_type == "guide_shown":
        guide_id = ctx.get("guide_id")
        if guide_id:
            shown = set(session_data.get("shown_guides") or [])
            shown.add(guide_id)
            session_data["shown_guides"] = list(shown)

    # ガイド却下（「後で確認する」選択時）— shown としてマーク
    elif event_type == "guide_dismissed":
        guide_id = ctx.get("guide_id")
        if guide_id:
            shown = set(session_data.get("shown_guides") or [])
            shown.add(guide_id)
            session_data["shown_guides"] = list(shown)

    # ボタンクリック記録
    elif event_type == "button_click":
        actions = list(session_data.get("actions_taken") or [])
        actions.append({
            "type":   "button_click",
            "target": ctx.get("target", ""),
            "port":   str(ctx.get("port", "")),
        })
        session_data["actions_taken"] = actions[-50:]

    _save_session(req.session_id, session_data)
    return {"ok": True}


# ── チャットウィジェット設定 CRUD ─────────────────────────────────────────────
_CONFIG_PORTS: list[dict] = [
    {"port": "8000", "label": "プラットフォーム"},
    {"port": "8011", "label": "AI 該非判定"},
    {"port": "8002", "label": "品目管理"},
    {"port": "8003", "label": "R&D リスク管理"},
    {"port": "8004", "label": "特許検索"},
    {"port": "8005", "label": "スクリーニング"},
    {"port": "8006", "label": "HS コード判定"},
]



# ── モジュール別コーチングプロンプト・テンプレート ────────────────────────────
_COACHING_TEMPLATES: dict[str, dict] = {
    "8011": {
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
            "1. 標準ワークフロー: R&D審査(8003) → 品目管理(8002) → AI該非判定(8011) → スクリーニング(8005)\n"
            "2. 新規案件は R&D リスク管理から始めることを推奨してください\n"
            "3. NeuroSymbolic 該非判定エージェントは AI 該非判定モジュール(8011)の取引詳細から起動できます\n"
            "4. 各モジュールが連携してデータを共有していることを説明してください\n"
            "5. UC一覧（📋ボタン）から「海外品目管理(UC3)」「BOM管理(UC7)」「出荷ライセンス管理(UC8)」も選べることを案内する\n"
            "6. ダッシュボードのアラート（ライセンス残高・期限切れ・規制動向）を定期確認するよう促してください"
        ),
    },
    "export_license": {
        "name": "輸出許可申請 — ライセンス管理フォーカス",
        "prompt_supplement": (
            "輸出許可申請管理画面では出荷ライセンスの残高・期限管理が最重要です（UC8）:\n"
            "1. 許可証4要素（品目/仕向地/需要者/用途）に変更がある場合は再申請が必要であることを伝える\n"
            "2. 残存許可額が20%以下でアラート状態の場合: BIS申請は2〜3ヶ月かかるため即座に再申請を促す\n"
            "3. 出荷のたびに「価値控除」フォームで残高を更新しないと次の出荷ができなくなることを強調する\n"
            "4. バリューベース許可証とユニットベース許可証の違いを案内する（ユニット: 数量管理）\n"
            "5. 許可証番号は出荷書類（インボイス・輸出申告書）に必ず記載が必要であることを伝える"
        ),
    },
    "supply_chain": {
        "name": "サプライチェーン管理 — BOM・De Minimisフォーカス",
        "prompt_supplement": (
            "サプライチェーン管理ではBOMとDe Minimis管理が主目的です（UC7）:\n"
            "1. サプライヤー申告のポータルURL発行後、提出期限を明示してサプライヤーへ送付するよう促す\n"
            "2. De Minimis比率が表示されたら: 一般25%・テロ支援国10%の閾値と比較して判断を案内する\n"
            "3. 米国サプライヤーの品目は特にFDPR適用の有無を確認するよう注意喚起する\n"
            "4. BOMに変更がある場合（部品変更・調達先変更）は再計算が必要であることを強調する\n"
            "5. 原産地証明書（CO）の有効期限（通常12ヶ月）の確認を促す"
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
