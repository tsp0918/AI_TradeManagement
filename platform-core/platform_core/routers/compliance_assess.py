"""
compliance_assess.py — AI コンプライアンス自動リスク評価エンジン

POST /api/compliance/assess
  → Layer A（規制）+ Layer D（技術論文）+ Layer F（制裁執行）+ 仕向国 + 外為法
    の5軸を並列検索し、総合リスクスコアと推奨アクションを返す。

POST /api/compliance/hantei
  → FAISS で規制候補を取得し、Ollama (qwen2.5:7b) が各候補の実質的な適用可否を推論する。
    テキストマイニングではなく AI 規制審査として機能させる。

R&D・品目管理・取引審査・顧客スクリーニング の全モジュール UI から呼び出す。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

_SELF_URL = os.getenv("MODULE_PLATFORM_URL", "http://localhost:8000")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/compliance", tags=["compliance"])

# ── 仕向国リスクマップ ─────────────────────────────────────────────────────────
_COUNTRY_RISK: dict[str, dict[str, Any]] = {
    "KP": {"score": 1.00, "level": "critical", "detail": "北朝鮮 — 包括的輸出禁止（EAR §746.4、OFAC SDN）"},
    "IR": {"score": 0.95, "level": "critical", "detail": "イラン — 包括的制裁（ITSR/OFAC、EAR §746.7）"},
    "SY": {"score": 0.95, "level": "critical", "detail": "シリア — 包括的制裁（OFAC、EAR §746.9）"},
    "RU": {"score": 0.92, "level": "critical", "detail": "ロシア — CAPTA/CIPA/SDN・FDPR適用（EAR §746.8）"},
    "BY": {"score": 0.88, "level": "high",     "detail": "ベラルーシ — EAR §746.8・EU制裁"},
    "CU": {"score": 0.87, "level": "high",     "detail": "キューバ — CACR（EAR §746.2）"},
    "CN": {"score": 0.82, "level": "high",     "detail": "中国 — EAR §746.3・FDPR・AI半導体輸出規制"},
    "SD": {"score": 0.80, "level": "high",     "detail": "スーダン — OFAC制裁"},
    "MM": {"score": 0.75, "level": "high",     "detail": "ミャンマー — OFAC/EU制裁"},
    "VE": {"score": 0.70, "level": "high",     "detail": "ベネズエラ — OFAC制裁（VENEZUELA）"},
    "IQ": {"score": 0.55, "level": "medium",   "detail": "イラク — 特定品目要注意"},
    "AF": {"score": 0.55, "level": "medium",   "detail": "アフガニスタン — 不安定地域"},
    "HK": {"score": 0.50, "level": "medium",   "detail": "香港 — FDPR・EAR規制強化（2020年以降）"},
}
_DEFAULT_COUNTRY = {"score": 0.10, "level": "low", "detail": ""}

# 外為法リスクが高い source_type
_FEFTA_TYPES = frozenset(["law", "tsutatsu", "parameter", "jp_fx_2025"])
# 規制リスクが高い source_type (ECCN/ITAR/EAR)
_ECCN_TYPES  = frozenset(["eccn", "eccn_detail", "usml_itar", "ear_744", "fdpr", "chips_act", "bis_semicon"])
# 制裁リスクレベル重み
_SANCTION_WEIGHT = {"critical": 1.0, "high": 0.80, "medium": 0.55, "low": 0.30}

# 総合スコア重み（和=1.0）
_W = {"regulation": 0.30, "fefta": 0.20, "sanctions": 0.25, "country": 0.15, "technology": 0.10}

# ── Ollama ローカル LLM 設定 ──────────────────────────────────────────────────
_OLLAMA_URL   = "http://localhost:11434/api/chat"
_OLLAMA_MODEL = "qwen2.5:7b"
_OLLAMA_SYSTEM = (
    "あなたは輸出管理コンプライアンス専門家（日本・米国）です。"
    "品目情報と規制テキストを照合し、規制の実質的な適用可否をJSON形式のみで回答します。"
    "規制テキストの具体的な技術的定義と品目の技術的特性を厳密に照合してください。"
    "セマンティックな類似性ではなく、実質的な規制適用可否（技術パラメータ・用途・対象カテゴリの合致）を判定します。"
)

# ── 文脈考慮スコアリング ───────────────────────────────────────────────────────

# ECCN/規制データのソース種別ごとの信頼性重み（ECCN直接データ > 相互承認データ > 法令一般）
_SOURCE_WEIGHTS: dict[str, float] = {
    "eccn_detail":           1.00,
    "eccn":                  0.85,
    "eccn_tech":             0.82,
    "usml_itar":             0.90,
    "ear_744":               0.90,
    "fdpr":                  0.95,
    "chips_act":             0.88,
    "bis_semicon":           0.85,
    "eu_dual_use_expanded":  0.65,
    "australia_dsgl":        0.55,
    "law":                   0.50,
    "tsutatsu":              0.50,
    "parameter":             0.45,
    "jp_fx_2025":            0.48,
}

# ECCN規制テキストの構造的な節タイトル（品目を特定しない汎用ラベル）
_GENERIC_LABELS = frozenset({
    "items_controlled", "items_excluded", "items_not_covered",
    "related_controls", "note", "technical_notes", "technical note",
    "license_exceptions", "license exceptions", "special_conditions",
    "a", "b", "c", "d", "e", "f", "g",
    "1", "2", "3", "4", "5", "6", "7", "8", "9",
    "i", "ii", "iii", "iv", "v",
})


def _context_score(h: dict, product_eccn: str = "") -> float:
    """
    FAISS コサイン類似度（0.78〜0.92 に集中）を、実務上の規制関連性スコア（0.0〜1.0）に変換する。

    - 再正規化: e5-large の典型的スコア帯 [0.78, 0.93] → [0.0, 1.0]
    - ソース重み: ECCN直接データ（eccn_detail）が最高、一般法令は低め
    - 汎用ラベルペナルティ: "items_controlled", "2" などの構造的ヘッダーは 0.5 倍
    - ECCN一致ボーナス: 品目のECCNと item_no が前方一致すれば 2.8 倍（上限1.0）
    """
    raw = float(h.get("score", 0.0))
    item_no    = str(h.get("item_no", "") or "").strip()
    item_label = str(h.get("title", "") or h.get("item_label", "") or "").lower().strip()
    source_type = str(h.get("source_type", ""))

    # Step 1: re-normalize from [0.78, 0.93] → [0.0, 1.0]
    normalized = max(0.0, min(1.0, (raw - 0.78) / 0.15))

    # Step 2: source type weight
    sw = _SOURCE_WEIGHTS.get(source_type, 0.40)

    # Step 3: generic structural label penalty
    generic = (item_label in _GENERIC_LABELS) or (item_label.isdigit() and len(item_label) <= 2)
    gp = 0.5 if generic else 1.0

    # Step 4: ECCN item_no specificity bonus
    eccn_bonus = 1.0
    if product_eccn and item_no:
        # Strip trailing letter suffix for comparison (e.g. "2B001" from "2B001.a.1")
        eccn_clean = product_eccn.upper().strip()
        item_clean = item_no.upper().strip()
        if item_clean.startswith(eccn_clean):
            eccn_bonus = 2.8   # 直接一致 — 要精査
        elif len(eccn_clean) >= 2 and item_clean[:2] == eccn_clean[:2]:
            eccn_bonus = 1.3   # 同一CCL大分類

    return round(min(normalized * sw * gp * eccn_bonus, 1.0), 3)


# ── リクエスト / レスポンス ───────────────────────────────────────────────────

class AssessRequest(BaseModel):
    query: str = Field(..., description="品目説明・技術キーワード")
    counterparty: str = Field("", description="取引相手企業名（制裁検索用）")
    country: str     = Field("", description="仕向国 ISO コード (例: CN, RU)")
    eccn: str        = Field("", description="既知の ECCN（オプション）")
    hs_code: str     = Field("", description="既知の HS コード（オプション）")
    context: str     = Field("transaction", description="rd | transaction | item")
    product_eccn: str = Field("", description="品目登録済みECCN（文脈スコアリング用）")

class ComponentResult(BaseModel):
    score: float
    level: str   # critical / high / medium / low
    detail: str = ""
    hits: list[dict[str, Any]] = []

class AssessResponse(BaseModel):
    overall_risk: float
    risk_level: str
    risk_color: str
    components: dict[str, ComponentResult]
    action_required: list[str]
    query: str
    context: str


def _level(score: float) -> str:
    if score >= 0.80: return "critical"
    if score >= 0.60: return "high"
    if score >= 0.35: return "medium"
    return "low"

def _color(level: str) -> str:
    return {"critical": "#dc2626", "high": "#f97316", "medium": "#eab308", "low": "#22c55e"}.get(level, "#6b7280")


async def _fetch_layer(client: httpx.AsyncClient, path: str, params: dict) -> list[dict]:
    try:
        r = await client.get(f"{_SELF_URL}{path}", params=params, timeout=10.0)
        if r.status_code != 200:
            return []
        return r.json().get("hits", [])
    except Exception as e:
        logger.warning("FAISS HTTP fetch error %s: %s", path, e)
        return []


def _build_regulation(hits_a: list[dict], product_eccn: str = "") -> ComponentResult:
    """Layer A ECCN系ヒットから規制リスクを算出（文脈考慮スコア使用）。"""
    eccn_hits = [h for h in hits_a if h.get("source_type") in _ECCN_TYPES]
    if not eccn_hits:
        return ComponentResult(score=0.0, level="low", detail="規制対象外 (EAR99 相当の可能性)")
    # 文脈考慮スコアを適用してソート
    scored = sorted(eccn_hits, key=lambda h: _context_score(h, product_eccn), reverse=True)
    score = _context_score(scored[0], product_eccn)
    top3 = scored[:3]
    return ComponentResult(
        score=round(score, 3),
        level=_level(score),
        detail=f"CCL/ECCN 規制テキスト {len(eccn_hits)} 件マッチ（文脈スコア最高: {score:.0%}）",
        hits=[{
            "source_type": h.get("source_type", ""),
            "title": (h.get("title", "") or h.get("item_no", ""))[:60],
            "item_no": h.get("item_no", ""),
            "text":  (h.get("full_text", "") or h.get("chunk_text", ""))[:120],
            "score": round(_context_score(h, product_eccn), 3),
        } for h in top3],
    )


def _build_fefta(hits_a: list[dict], product_eccn: str = "") -> ComponentResult:
    """Layer A 外為法系ヒットから外為法リスクを算出（文脈考慮スコア使用）。"""
    fefta_hits = [h for h in hits_a if h.get("source_type") in _FEFTA_TYPES]
    if not fefta_hits:
        return ComponentResult(score=0.0, level="low", detail="外為法該当なし（暫定）")
    scored = sorted(fefta_hits, key=lambda h: _context_score(h, product_eccn), reverse=True)
    score = _context_score(scored[0], product_eccn)
    top3 = scored[:3]
    return ComponentResult(
        score=round(score, 3),
        level=_level(score),
        detail=f"外為法・通達 {len(fefta_hits)} 件マッチ（文脈スコア最高: {score:.0%}）",
        hits=[{
            "source_type": h.get("source_type", ""),
            "title":  (h.get("title", "") or h.get("item_no", ""))[:60],
            "item_no": h.get("item_no", ""),
            "text":   (h.get("full_text", "") or "")[:120],
            "score":  round(_context_score(h, product_eccn), 3),
        } for h in top3],
    )


def _build_sanctions(hits_f: list[dict]) -> ComponentResult:
    """Layer F 制裁執行事例から制裁リスクを算出。"""
    if not hits_f:
        return ComponentResult(score=0.0, level="low", detail="類似制裁事例なし")
    top = hits_f[0]
    raw  = top.get("score", 0.0)
    rl   = top.get("risk_level", "medium")
    score = min(raw * _SANCTION_WEIGHT.get(rl, 0.5), 1.0)
    return ComponentResult(
        score=round(score, 3),
        level=_level(score),
        detail=f"類似制裁事例 {len(hits_f)} 件（最高スコア {raw:.2f}）",
        hits=[{
            "case_id":     h.get("case_id", ""),
            "entity":      h.get("entity", ""),
            "source_type": h.get("source_type", ""),
            "risk_level":  h.get("risk_level", ""),
            "authority":   h.get("authority", ""),
            "text":        h.get("full_text", "")[:150],
            "score":       round(h.get("score", 0), 3),
        } for h in hits_f[:3]],
    )


def _build_country(country: str) -> ComponentResult:
    info = _COUNTRY_RISK.get(country.upper(), _DEFAULT_COUNTRY)
    return ComponentResult(
        score=info["score"],
        level=info["level"],
        detail=info.get("detail", "") or f"{country} — 規制リスク低（EAR99等の基本ライセンス要件を確認）",
    )


def _build_technology(hits_d: list[dict]) -> ComponentResult:
    """Layer D 先端技術論文数から技術機密度を算出。"""
    high_cite = [h for h in hits_d if h.get("score", 0) >= 0.75]
    count = len(high_cite)
    score = min(count * 0.12, 0.60)
    return ComponentResult(
        score=round(score, 3),
        level=_level(score),
        detail=f"関連先端研究論文 {count} 件（スコア≥0.75）" if count else "先端研究論文との高類似なし",
        hits=[{
            "title":        h.get("title", "")[:80],
            "year":         h.get("year"),
            "eccn_tags":    h.get("eccn_tags", [])[:3],
            "score":        round(h.get("score", 0), 3),
        } for h in high_cite[:3]],
    )


def _actions(
    regulation: ComponentResult,
    fefta: ComponentResult,
    sanctions: ComponentResult,
    country: ComponentResult,
) -> list[str]:
    acts: list[str] = []
    if country.score >= 0.85:
        acts.append("輸出許可申請が必要（EAR §746 / 外為法特別な包括許可不可）")
    elif country.score >= 0.60:
        acts.append("仕向国の輸出許可要件を確認すること")
    if sanctions.score >= 0.65:
        acts.append("取引相手の制裁リスト照合を必ず実施（制裁執行事例に類似パターンあり）")
    if regulation.score >= 0.75:
        acts.append("ECCN 該非判定を確定してから輸出手続きに進むこと")
    if fefta.score >= 0.65:
        acts.append("外為法 第1項〜第15項の該当性を輸出管理責任者に確認すること")
    if regulation.score >= 0.85 and country.score >= 0.70:
        acts.append("ライセンス例外適用可否を精査すること（NLR・STA・TMP 等）")
    if not acts:
        acts.append("現時点で重大なリスクフラグなし — 通常手続きで進めること")
    return acts


@router.post("/assess", response_model=AssessResponse)
async def assess(req: AssessRequest) -> AssessResponse:
    """
    5軸 AI コンプライアンスリスク評価。

    Layer A（CCL/外為法）+ Layer D（技術論文）+ Layer F（制裁執行）
    + 仕向国リスクマップ の並列検索から総合スコアを算出する。
    """
    query_a = f"{req.eccn} {req.query}".strip() if req.eccn else req.query
    query_f = f"{req.counterparty} {req.query}".strip() if req.counterparty else req.query

    async with httpx.AsyncClient() as client:
        hits_a, hits_d, hits_f = await asyncio.gather(
            _fetch_layer(client, "/api/faiss/search/layer-a", {"q": query_a[:300], "top_k": 10}),
            _fetch_layer(client, "/api/faiss/search/layer-d", {"q": req.query[:300], "top_k": 8}),
            _fetch_layer(client, "/api/faiss/search/layer-f", {"q": query_f[:300], "top_k": 5}),
        )

    _eccn_ctx  = req.product_eccn or req.eccn
    regulation = _build_regulation(hits_a, _eccn_ctx)
    fefta      = _build_fefta(hits_a, _eccn_ctx)
    sanctions  = _build_sanctions(hits_f)
    country    = _build_country(req.country)
    technology = _build_technology(hits_d)

    overall = (
        _W["regulation"] * regulation.score
        + _W["fefta"]      * fefta.score
        + _W["sanctions"]  * sanctions.score
        + _W["country"]    * country.score
        + _W["technology"] * technology.score
    )
    overall = round(min(overall, 1.0), 3)
    level   = _level(overall)

    return AssessResponse(
        overall_risk=overall,
        risk_level=level,
        risk_color=_color(level),
        components={
            "regulation": regulation,
            "fefta":      fefta,
            "sanctions":  sanctions,
            "country":    country,
            "technology": technology,
        },
        action_required=_actions(regulation, fefta, sanctions, country),
        query=req.query,
        context=req.context,
    )


# ── 該非判定候補ティア別取得 ──────────────────────────────────────────────────

class HanteiItem(BaseModel):
    item_no: str
    item_label: str
    source_type: str
    source_name: str
    raw_score: float
    adj_score: float
    tier: str               # "critical" | "review" | "info"
    regulation_text: str
    decision: str           # "controlled" | "needs_review" | "non_controlled"
    llm_verdict: str = "SKIPPED"     # "APPLICABLE" | "REVIEW_NEEDED" | "NOT_APPLICABLE" | "SKIPPED"
    llm_confidence: str = ""         # "HIGH" | "MEDIUM" | "LOW"
    llm_reason: str = ""             # AI 判定根拠（日本語）
    llm_key_question: str = ""       # 追加確認事項


class HanteiResponse(BaseModel):
    tiers: dict[str, list[HanteiItem]]  # {"critical":[], "review":[], "info":[]}
    excluded: list[HanteiItem] = []     # NOT_APPLICABLE items (high FAISS score, but Ollama said not applicable)
    product_eccn: str
    query: str
    total_hits: int
    llm_evaluated: int = 0
    llm_excluded: int = 0
    llm_summary: str = ""


async def _ollama_evaluate_hit(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    product_desc: str,
    product_eccn: str,
    hs_code: str,
    h: dict,
) -> dict:
    """
    Ollama qwen2.5:7b で1件の規制候補の実質的な適用可否を推論する。

    戻り値: {"verdict": "APPLICABLE|REVIEW_NEEDED|NOT_APPLICABLE",
              "confidence": "HIGH|MEDIUM|LOW",
              "reason": "...", "key_question": "..."}
    """
    item_no     = str(h.get("item_no", ""))
    source_type = str(h.get("source_type", ""))
    item_label  = str(h.get("title", "") or h.get("item_label", "") or "")
    reg_text    = str(h.get("full_text", "") or h.get("chunk_text", ""))[:600]

    user_prompt = (
        f"## 審査品目\n"
        f"品目名/説明: {product_desc[:300]}\n"
        f"ECCN（品目分類）: {product_eccn or '未設定'}\n"
        f"HSコード: {hs_code or '未設定'}\n\n"
        f"## 規制候補 [{item_no}] {item_label}\n"
        f"ソース種別: {source_type}\n"
        f"規制テキスト:\n{reg_text}\n\n"
        f"## 回答フォーマット（JSONのみ・他テキスト不可）\n"
        f'{{"verdict": "APPLICABLE|REVIEW_NEEDED|NOT_APPLICABLE", '
        f'"confidence": "HIGH|MEDIUM|LOW", '
        f'"reason": "判定根拠1〜2文（日本語）", '
        f'"key_question": "確認すべき技術パラメータや条件（不要なら空文字）"}}\n\n'
        f"判定基準:\n"
        f"APPLICABLE — 品目の技術的特性・用途が規制定義に実質的に合致する\n"
        f"REVIEW_NEEDED — 適用可否の判断に追加の技術情報が必要\n"
        f"NOT_APPLICABLE — 品目と規制が技術的に明らかに無関係（異なる技術カテゴリ・分野）"
    )

    async with sem:
        try:
            r = await client.post(
                _OLLAMA_URL,
                json={
                    "model": _OLLAMA_MODEL,
                    "messages": [
                        {"role": "system", "content": _OLLAMA_SYSTEM},
                        {"role": "user",   "content": user_prompt},
                    ],
                    "format": "json",
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 300},
                },
                timeout=90.0,
            )
            parsed = json.loads(r.json()["message"]["content"])
            if parsed.get("verdict") not in ("APPLICABLE", "REVIEW_NEEDED", "NOT_APPLICABLE"):
                parsed["verdict"] = "REVIEW_NEEDED"
            return parsed
        except Exception as e:
            logger.warning("Ollama hantei failed [%s]: %s", item_no, e)
            return {
                "verdict": "REVIEW_NEEDED",
                "confidence": "LOW",
                "reason": "AI評価を実行できませんでした（フォールバック）",
                "key_question": "",
            }


@router.post("/hantei", response_model=HanteiResponse)
async def hantei(req: AssessRequest) -> HanteiResponse:
    """
    該非判定候補を Ollama ローカル LLM で実質的に評価してティア分類する。

    フロー:
    1. FAISS Layer A から top-20 を取得
    2. adj_score >= 0.20 でプレフィルタ、item_no 重複除去、top-8 を選定
    3. qwen2.5:7b で各候補の規制適用可否を並列推論（最大3同時）
    4. NOT_APPLICABLE を除外、APPLICABLE/REVIEW_NEEDED のみ tier に分類して返す
    """
    product_eccn = req.product_eccn or req.eccn
    query_a = f"{product_eccn} {req.query}".strip() if product_eccn else req.query

    async with httpx.AsyncClient(timeout=15.0) as client:
        hits_a = await _fetch_layer(
            client, "/api/faiss/search/layer-a", {"q": query_a[:300], "top_k": 20}
        )

    # Pre-filter: adj_score >= 0.20, deduplicate by item_no, take top-8
    scored = [(h, _context_score(h, product_eccn)) for h in hits_a]
    seen_keys: set[str] = set()
    unique_candidates: list[dict] = []
    for h, s in sorted(scored, key=lambda x: x[1], reverse=True):
        if s < 0.20:
            continue
        key = str(h.get("item_no", "")).strip() or str(h.get("title", "")).strip()
        if key and key in seen_keys:
            continue
        if key:
            seen_keys.add(key)
        unique_candidates.append(h)
        if len(unique_candidates) >= 8:
            break

    # Ollama parallel LLM evaluation (max 3 concurrent)
    sem = asyncio.Semaphore(3)
    async with httpx.AsyncClient(timeout=120.0) as ollama_client:
        tasks = [
            _ollama_evaluate_hit(ollama_client, sem, req.query, product_eccn, req.hs_code, h)
            for h in unique_candidates
        ]
        llm_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Build tiers from LLM verdicts
    tiers: dict[str, list[HanteiItem]] = {"critical": [], "review": [], "info": []}
    excluded_items: list[HanteiItem] = []
    llm_excluded = 0

    for h, result in zip(unique_candidates, llm_results):
        if isinstance(result, Exception):
            result = {"verdict": "REVIEW_NEEDED", "confidence": "LOW",
                      "reason": "AI評価エラー", "key_question": ""}

        verdict    = str(result.get("verdict",      "REVIEW_NEEDED"))
        confidence = str(result.get("confidence",   "LOW"))
        reason     = str(result.get("reason",       ""))
        key_q      = str(result.get("key_question", ""))
        adj        = _context_score(h, product_eccn)
        item_label = str(h.get("title", "") or h.get("item_label", "") or h.get("item_no", "")).strip()

        if verdict == "NOT_APPLICABLE":
            llm_excluded += 1
            excluded_items.append(HanteiItem(
                item_no=str(h.get("item_no", "")),
                item_label=item_label,
                source_type=str(h.get("source_type", "")),
                source_name=str(h.get("source_name", "")),
                raw_score=round(float(h.get("score", 0)), 3),
                adj_score=adj,
                tier="excluded",
                regulation_text=str(h.get("full_text", "") or h.get("chunk_text", ""))[:300],
                decision="non_controlled",
                llm_verdict="NOT_APPLICABLE",
                llm_confidence=confidence,
                llm_reason=reason,
                llm_key_question=key_q,
            ))
            continue

        # Tier from LLM verdict
        if verdict == "APPLICABLE":
            tier = "critical" if confidence in ("HIGH", "MEDIUM") else "review"
        else:
            tier = "review"

        tiers[tier].append(HanteiItem(
            item_no=str(h.get("item_no", "")),
            item_label=item_label,
            source_type=str(h.get("source_type", "")),
            source_name=str(h.get("source_name", "")),
            raw_score=round(float(h.get("score", 0)), 3),
            adj_score=adj,
            tier=tier,
            regulation_text=str(h.get("full_text", "") or h.get("chunk_text", ""))[:300],
            decision="controlled" if tier == "critical" else "needs_review",
            llm_verdict=verdict,
            llm_confidence=confidence,
            llm_reason=reason,
            llm_key_question=key_q,
        ))

    total_evaluated = len(unique_candidates)
    shown = sum(len(v) for v in tiers.values())
    llm_summary = (
        f"AI が {total_evaluated} 件を審査 → "
        f"{llm_excluded} 件を非該当として除外、"
        f"{shown} 件が要確認候補"
    )

    return HanteiResponse(
        tiers=tiers,
        excluded=excluded_items,
        product_eccn=product_eccn,
        query=req.query,
        total_hits=total_evaluated,
        llm_evaluated=total_evaluated,
        llm_excluded=llm_excluded,
        llm_summary=llm_summary,
    )


# ── 外部AI判定 matrix_matches_top を Ollama で再評価 ──────────────────────────

class FilterItem(BaseModel):
    item_no: str = ""
    item_label: str = ""
    source_type: str = ""
    regulation_text: str = ""   # evidence.text_snippet
    usage_text: str = ""        # evidence.usage_text（品目の用途記述）
    match_score: float = 0.0


class FilterItemResult(BaseModel):
    item_no: str
    item_label: str
    source_type: str
    regulation_text: str
    usage_text: str
    match_score: float
    llm_verdict: str
    llm_confidence: str
    llm_reason: str
    llm_key_question: str
    tier: str      # "critical" | "review"


class FilterItemsRequest(BaseModel):
    items: list[FilterItem]
    product_desc: str = Field("", description="品目名・説明")
    product_eccn: str = Field("", description="品目ECCN")
    hs_code: str      = Field("", description="品目HSコード")


class FilterItemsResponse(BaseModel):
    critical: list[FilterItemResult]
    review:   list[FilterItemResult]
    llm_evaluated: int
    llm_excluded: int
    llm_summary: str


async def _ollama_evaluate_filter_item(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    product_desc: str,
    product_eccn: str,
    hs_code: str,
    item: FilterItem,
) -> dict:
    """matrix_matches_top の1件を Ollama で評価する（_ollama_evaluate_hit の dict 版）。"""
    h = {
        "item_no":    item.item_no,
        "title":      item.item_label,
        "source_type": item.source_type,
        "full_text":  f"{item.regulation_text}\n用途: {item.usage_text}".strip(),
    }
    return await _ollama_evaluate_hit(client, sem, product_desc, product_eccn, hs_code, h)


@router.post("/filter-items", response_model=FilterItemsResponse)
async def filter_items(req: FilterItemsRequest) -> FilterItemsResponse:
    """
    外部AI判定の matrix_matches_top を Ollama で再評価し、実質該当のみ返す。

    - 入力: matrix_matches_top の各 evidence フィールドをマッピングした items リスト
    - 処理: qwen2.5:7b が各候補の規制適用可否を推論
    - 出力: APPLICABLE → critical/review、NOT_APPLICABLE → 除外
    - 目的: 生 FAISS スコア 0.75 閾値による全件 "hit" 問題を解消
    """
    # top-8 unique by item_no
    seen: set[str] = set()
    candidates: list[FilterItem] = []
    for it in sorted(req.items, key=lambda x: x.match_score, reverse=True):
        key = it.item_no or it.item_label
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        candidates.append(it)
        if len(candidates) >= 8:
            break

    sem = asyncio.Semaphore(3)
    async with httpx.AsyncClient(timeout=120.0) as ollama_client:
        tasks = [
            _ollama_evaluate_filter_item(
                ollama_client, sem,
                req.product_desc, req.product_eccn, req.hs_code, it
            )
            for it in candidates
        ]
        llm_results = await asyncio.gather(*tasks, return_exceptions=True)

    critical: list[FilterItemResult] = []
    review:   list[FilterItemResult] = []
    llm_excluded = 0

    for it, result in zip(candidates, llm_results):
        if isinstance(result, Exception):
            result = {"verdict": "REVIEW_NEEDED", "confidence": "LOW",
                      "reason": "AI評価エラー", "key_question": ""}

        verdict    = str(result.get("verdict",      "REVIEW_NEEDED"))
        confidence = str(result.get("confidence",   "LOW"))
        reason     = str(result.get("reason",       ""))
        key_q      = str(result.get("key_question", ""))

        if verdict == "NOT_APPLICABLE":
            llm_excluded += 1
            continue

        tier = ("critical"
                if verdict == "APPLICABLE" and confidence in ("HIGH", "MEDIUM")
                else "review")

        rec = FilterItemResult(
            item_no=it.item_no, item_label=it.item_label,
            source_type=it.source_type, regulation_text=it.regulation_text,
            usage_text=it.usage_text, match_score=it.match_score,
            llm_verdict=verdict, llm_confidence=confidence,
            llm_reason=reason, llm_key_question=key_q,
            tier=tier,
        )
        if tier == "critical":
            critical.append(rec)
        else:
            review.append(rec)

    total = len(candidates)
    shown = len(critical) + len(review)
    summary = (
        f"AI が {total} 件を精査 → "
        f"{llm_excluded} 件を非該当として除外、"
        f"{shown} 件が該当候補"
    )

    return FilterItemsResponse(
        critical=critical, review=review,
        llm_evaluated=total, llm_excluded=llm_excluded,
        llm_summary=summary,
    )
