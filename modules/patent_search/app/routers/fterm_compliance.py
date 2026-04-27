"""
fterm_compliance.py — Fターム → 輸出規制（ECCN / 外為法）照合 API
──────────────────────────────────────────────────────────────────────────────
JPO Fタームテーマコードから対応するECCN / 外為法省令項番を返す。
特許のFターム分類から輸出規制上の注意点を自動提示する。

エンドポイント:
  GET /api/fterm-compliance/{fterm_code}
    単一Fタームコードの規制照合結果を返す。

  POST /api/fterm-compliance/batch
    複数Fタームコードを一括照合する（特許1件のFターム全体を渡す想定）。

  GET /api/fterm-compliance/eccn/{eccn}
    ECCNに対応するFタームコード一覧を返す（逆引き）。

  GET /api/fterm-compliance/stats
    マッピングデータの統計情報を返す。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

_ROOT         = Path(__file__).resolve().parents[4]
_MAPPING_PATH = _ROOT / "data" / "staging" / "fterm_eccn_mapping.json"


@lru_cache(maxsize=1)
def _load_mapping() -> dict[str, Any]:
    if not _MAPPING_PATH.exists():
        return {"fterm_to_eccn_index": {}, "eccn_to_fterm_index": {}, "total": 0}
    return json.loads(_MAPPING_PATH.read_text(encoding="utf-8"))


def _normalize_fterm(code: str) -> str:
    """Fタームコードを正規化（大文字・空白除去）。"""
    return code.strip().upper()


def _match_fterm(code: str, idx: dict) -> dict | None:
    """完全一致 → 前方一致（5文字プレフィックス）の順で照合。"""
    if code in idx:
        return {"fterm": code, "entries": idx[code], "match_type": "exact"}
    # 数字5文字+アルファニュメリックの場合、先頭5文字で前方一致
    prefix5 = code[:5] if len(code) >= 5 else code
    for key in idx:
        if key.startswith(prefix5) and key != code:
            return {"fterm": key, "entries": idx[key], "match_type": "prefix", "original": code}
    # IPC/CPC形式（英字+数字）で前方一致
    for key in idx:
        if code.startswith(key) or key.startswith(code[:4]):
            return {"fterm": key, "entries": idx[key], "match_type": "partial", "original": code}
    return None


# ── レスポンスモデル ────────────────────────────────────────────────────────────

class FtermEntry(BaseModel):
    eccn: str
    fefta: str | None = None
    confidence: str
    rationale: str | None = None


class FtermComplianceResult(BaseModel):
    fterm_code: str
    matched_key: str | None = None
    match_type: str | None = None
    entries: list[FtermEntry] = []
    high_risk_eccns: list[str] = []
    fefta_items: list[str] = []
    found: bool = False


class BatchRequest(BaseModel):
    fterm_codes: list[str]


# ── エンドポイント ─────────────────────────────────────────────────────────────
# 注意: 固定パス（/stats, /eccn/xxx）は動的パス（/{fterm_code}）より前に定義すること

@router.get("/fterm-compliance/stats",
            summary="Fタームマッピング統計")
async def get_fterm_stats():
    """Fターム → ECCN マッピングデータの統計情報を返す。"""
    mapping  = _load_mapping()
    fterm_idx = mapping.get("fterm_to_eccn_index", {})
    eccn_idx  = mapping.get("eccn_to_fterm_index", {})

    confidence_dist: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    for entries in fterm_idx.values():
        for e in entries:
            conf = e.get("confidence", "low")
            confidence_dist[conf] = confidence_dist.get(conf, 0) + 1

    eccn_counts = {eccn: len(ftms) for eccn, ftms in eccn_idx.items()}
    top_eccns = sorted(eccn_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "version":               mapping.get("_version", "unknown"),
        "updated":               mapping.get("_updated", "unknown"),
        "total_fterm_codes":     len(fterm_idx),
        "total_eccns_covered":   len(eccn_idx),
        "total_mappings":        mapping.get("total", 0),
        "confidence_dist":       confidence_dist,
        "top_eccns_by_coverage": [{"eccn": e, "fterm_count": c} for e, c in top_eccns],
    }


@router.get("/fterm-compliance/eccn/{eccn}",
            summary="ECCN → Fターム逆引き")
async def get_fterms_by_eccn(eccn: str):
    """指定ECCNに対応するJPO Fタームコード一覧を返す。"""
    mapping  = _load_mapping()
    idx      = mapping.get("eccn_to_fterm_index", {})
    eccn_key = eccn.strip().upper()

    entries = idx.get(eccn_key)
    if not entries:
        raise HTTPException(404, f"ECCN {eccn_key} に対応するFタームが見つかりません")

    return {"eccn": eccn_key, "total": len(entries), "fterms": entries}


_EN_JA_SUGGEST: dict[str, list[str]] = {
    "semiconductor": ["半導体", "トランジスタ", "fet"],
    "laser": ["レーザー", "光"],
    "cryptograph": ["暗号", "crypto"],
    "nuclear": ["核", "原子炉", "ウラン"],
    "missile": ["ミサイル", "ロケット"],
    "radar": ["レーダー", "radar"],
    "drone": ["無人機", "uav", "ドローン"],
    "chemical": ["化学", "chemical"],
    "biological": ["生物", "バイオ"],
    "aircraft": ["航空機", "エンジン"],
    "optic": ["光学", "光"],
    "sensor": ["センサー", "検出"],
    "robot": ["ロボット", "自動化"],
    "material": ["材料", "合金"],
    "computer": ["コンピュータ", "計算機"],
    "network": ["ネットワーク", "通信"],
}


@router.get("/fterm-compliance/suggest",
            summary="キーワードからFタームを提案")
async def suggest_fterms(keyword: str, limit: int = 5):
    """
    検索キーワードからFタームマッピングに含まれる関連コードを提案する。
    rationale テキストのキーワードマッチで上位 limit 件を返す。
    英語キーワードは日本語変換テーブルでも照合する。
    """
    mapping = _load_mapping()
    idx = mapping.get("fterm_to_eccn_index", {})
    kw = keyword.strip().lower()
    if not kw:
        return {"keyword": keyword, "suggestions": []}

    # 英語→日本語展開
    search_terms = [kw]
    for en_key, ja_list in _EN_JA_SUGGEST.items():
        if kw in en_key or en_key in kw:
            search_terms.extend(ja_list)

    scored: list[tuple[int, str, list]] = []
    for fterm, entries in idx.items():
        score = 0
        for e in entries:
            rat = (e.get("rationale") or "").lower()
            eccn = (e.get("eccn") or "").lower()
            for term in search_terms:
                score += rat.count(term) * 2
            score += 1 if kw in eccn else 0
        if score > 0:
            scored.append((score, fterm, entries))

    scored.sort(key=lambda x: -x[0])
    suggestions = [
        {
            "fterm": ft,
            "eccns": list({e["eccn"] for e in entries}),
            "fefta": list({e["fefta"] for e in entries if e.get("fefta")}),
            "score": sc,
        }
        for sc, ft, entries in scored[:limit]
    ]
    return {"keyword": keyword, "total": len(scored), "suggestions": suggestions}


@router.get("/fterm-compliance/{fterm_code}",
            response_model=FtermComplianceResult,
            summary="Fターム → ECCN / 外為法照合")
async def get_fterm_compliance(fterm_code: str):
    """
    Fタームコードから対応するECCN / 外為法省令項番を返す。

    例:
      GET /api/fterm-compliance/5F048  → ECCN 3A001（半導体デバイス）
      GET /api/fterm-compliance/5J104  → ECCN 5E002（暗号技術）
      GET /api/fterm-compliance/G21C   → ECCN 0A001（原子炉）
    """
    mapping = _load_mapping()
    idx     = mapping.get("fterm_to_eccn_index", {})
    code    = _normalize_fterm(fterm_code)

    match = _match_fterm(code, idx)
    if not match:
        return FtermComplianceResult(fterm_code=fterm_code, found=False)

    entries = [FtermEntry(**e) for e in match["entries"]]
    high_risk = [e.eccn for e in entries if e.confidence == "high"]
    fefta_items = list({e.fefta for e in entries if e.fefta})

    return FtermComplianceResult(
        fterm_code=fterm_code,
        matched_key=match["fterm"],
        match_type=match["match_type"],
        entries=entries,
        high_risk_eccns=high_risk,
        fefta_items=fefta_items,
        found=True,
    )


@router.post("/fterm-compliance/batch",
             summary="Fターム一括照合（特許1件分）")
async def batch_fterm_compliance(body: BatchRequest):
    """
    特許1件のFタームコードリストを渡し、規制関連フラグを一括返す。
    高規制リスクのECCNが1件でも見つかればフラグを立てる。
    """
    mapping = _load_mapping()
    idx     = mapping.get("fterm_to_eccn_index", {})

    results: list[dict] = []
    all_eccns: set[str] = set()
    all_fefta: set[str] = set()
    high_risk_ftermss: list[str] = []

    for raw_code in body.fterm_codes:
        code  = _normalize_fterm(raw_code)
        match = _match_fterm(code, idx)
        if not match:
            results.append({"fterm": raw_code, "found": False})
            continue

        entries   = match["entries"]
        high_risk = [e["eccn"] for e in entries if e.get("confidence") == "high"]
        fefta_l   = [e["fefta"] for e in entries if e.get("fefta")]

        all_eccns.update(e["eccn"] for e in entries)
        all_fefta.update(fefta_l)
        if high_risk:
            high_risk_ftermss.append(raw_code)

        results.append({
            "fterm":        raw_code,
            "matched_key":  match["fterm"],
            "match_type":   match["match_type"],
            "eccns":        list({e["eccn"] for e in entries}),
            "high_risk":    bool(high_risk),
            "fefta_items":  list(set(fefta_l)),
            "found":        True,
        })

    # サマリー
    risk_level = "none"
    if any(r.get("high_risk") for r in results):
        risk_level = "high"
    elif all_eccns:
        risk_level = "medium"

    return {
        "total_codes":     len(body.fterm_codes),
        "matched":         sum(1 for r in results if r.get("found")),
        "overall_risk":    risk_level,
        "eccns_detected":  sorted(all_eccns),
        "fefta_items":     sorted(all_fefta),
        "high_risk_fterms": high_risk_ftermss,
        "details":         results,
    }


