"""
regulatory.py — 規制動向モニタリング + グローバル規制レジーム照合 API

エンドポイント:
  GET  /api/regulatory/changes          未読変更一覧
  POST /api/regulatory/check            モニタリング実行（外部API ポーリング）
  POST /api/regulatory/changes/{id}/dismiss  既読マーク

  GET  /api/regulatory/usml/{category}  USML/ITARカテゴリ詳細
  GET  /api/regulatory/usml             USML全21カテゴリ一覧
  GET  /api/regulatory/eu-dual-use/{category}  EU Dual-Useカテゴリ詳細
  GET  /api/regulatory/eu-dual-use      EU Dual-Use全10カテゴリ一覧
  POST /api/regulatory/regime-check     品目説明からグローバル規制レジーム照合
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.db.session import get_db
from platform_core.models.regulatory_change import RegulatoryChange

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/regulatory", tags=["regulatory"])

_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "staging"


@lru_cache(maxsize=1)
def _load_usml() -> dict[str, Any]:
    p = _DATA_DIR / "usml_itar_entries.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"entries": []}


@lru_cache(maxsize=1)
def _load_eu_dual_use() -> dict[str, Any]:
    p = _DATA_DIR / "eu_dual_use_entries.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"entries": []}

# ── 設定 ───────────────────────────────────────────────────────────────────────
_EGOV_BASE = "https://laws.e-gov.go.jp/api/1"
_WATCH_LAWS = {
    "409AC0000000228": ("外国為替及び外国貿易法（外為法）", "meti_fefta"),
    "411CO0000000378": ("輸出貿易管理令",                   "meti_eccn"),
    "413M60400000049": ("外国為替令",                       "meti_fefta"),
}
_FED_REGISTER_API = "https://www.federalregister.gov/api/v1/documents.json"
_BIS_AGENCY_ID    = "bureau-of-industry-and-security"
_EAR_KEYWORDS     = [
    "Export Administration Regulations", "Entity List",
    "Commerce Control List", "export control",
]
# "EAR" は単語境界付きで別途チェック（search/clearance 等の誤マッチを防ぐ）
_EAR_WORD_RE = re.compile(r'\bear\b', re.IGNORECASE)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:32]


# ── エンドポイント ─────────────────────────────────────────────────────────────

@router.get("/changes")
async def list_changes(limit: int = 20, db: AsyncSession = Depends(get_db)):
    """未読（未dismiss）の規制変更一覧を返す。"""
    rows = await db.execute(
        select(RegulatoryChange)
        .where(RegulatoryChange.is_dismissed == False)  # noqa: E712
        .order_by(RegulatoryChange.detected_at.desc())
        .limit(limit)
    )
    changes = rows.scalars().all()
    return {
        "count": len(changes),
        "changes": [
            {
                "id":             c.id,
                "source":         c.source,
                "change_type":    c.change_type,
                "title":          c.title,
                "severity":       c.severity,
                "item_ref":       c.item_ref,
                "effective_date": c.effective_date,
                "source_url":     c.source_url,
                "detected_at":    c.detected_at.isoformat() if c.detected_at else None,
            }
            for c in changes
        ],
    }


@router.post("/check")
async def run_check(db: AsyncSession = Depends(get_db)):
    """外部API（e-Gov / BIS Federal Register）をポーリングして変更を検出する。"""
    egov_added = await _check_egov(db)
    bis_added  = await _check_bis(db)
    result = {
        "egov":  egov_added,
        "bis":   bis_added,
        "total": egov_added + bis_added,
    }
    logger.info("RegMonitor run: %s", result)
    return result


# ── USML/ITAR エンドポイント ────────────────────────────────────────────────────

@router.get("/usml")
async def list_usml():
    """ITAR/USML 全21カテゴリの一覧を返す。"""
    data = _load_usml()
    return {
        "total": data.get("total", 21),
        "regulation": data.get("_regulation", ""),
        "categories": [
            {
                "category":    e["usml_category"],
                "title":       e["title"],
                "fefta":       e.get("fefta_category"),
                "related_eccn": e.get("related_eccn", []),
            }
            for e in data.get("entries", [])
        ],
    }


@router.get("/usml/{category}")
async def get_usml_category(category: str):
    """指定したUSMLカテゴリの詳細を返す。例: /usml/VIII → 航空機"""
    data = _load_usml()
    cat  = category.strip().upper()
    for e in data.get("entries", []):
        if e["usml_category"].upper() == cat:
            return e
    raise HTTPException(404, f"USML Category {cat} not found")


# ── EU Dual-Use エンドポイント ───────────────────────────────────────────────────

@router.get("/eu-dual-use")
async def list_eu_dual_use():
    """EU Dual-Use Regulation 全10カテゴリの一覧を返す。"""
    data = _load_eu_dual_use()
    return {
        "total":      data.get("total_categories", 10),
        "regulation": data.get("_regulation", ""),
        "categories": [
            {
                "category":    e["eu_category"],
                "title":       e["title"],
                "fefta":       e.get("fefta_category"),
                "eccn_parallel": e.get("eccn_parallel", []),
            }
            for e in data.get("entries", [])
        ],
    }


@router.get("/eu-dual-use/{category}")
async def get_eu_dual_use_category(category: str):
    """指定したEU Dual-Useカテゴリの詳細を返す。例: /eu-dual-use/3 → Electronics"""
    data = _load_eu_dual_use()
    for e in data.get("entries", []):
        if str(e["eu_category"]) == category.strip():
            return e
    raise HTTPException(404, f"EU Dual-Use Category {category} not found")


# ── グローバル規制レジーム照合 ────────────────────────────────────────────────────

class RegimeCheckRequest(BaseModel):
    item_name: str
    description: str = ""
    eccn: str | None = None


@router.post("/regime-check")
async def regime_check(body: RegimeCheckRequest):
    """
    品目名・説明・ECCNから関連するグローバル規制レジームを照合する。

    チェック対象:
      - ITAR/USML: ECCNまたはキーワードでUSMLカテゴリを特定
      - EU Dual-Use: ECCNから対応するEUカテゴリを特定
      - MTCR: ミサイル・宇宙技術への適用確認
      - NSG: 核関連技術への適用確認
      - AG: 化学/生物兵器前駆物質への適用確認
    """
    usml_data  = _load_usml()
    eu_data    = _load_eu_dual_use()
    text_lower = (body.item_name + " " + body.description).lower()
    eccn_upper = (body.eccn or "").upper()

    # ECCN prefix → USML / EU カテゴリのマッピング
    eccn_to_usml: dict[str, list[str]] = {}
    for e in usml_data.get("entries", []):
        for related in e.get("related_eccn", []):
            eccn_to_usml.setdefault(related, []).append(e["usml_category"])

    eccn_to_eu: dict[str, list[str]] = {}
    for e in eu_data.get("entries", []):
        for parallel in e.get("eccn_parallel", []):
            eccn_to_eu.setdefault(parallel, []).append(e["eu_category"])

    flags: list[dict] = []

    # ITAR/USML チェック
    usml_hits: list[str] = []
    if eccn_upper:
        usml_hits = eccn_to_usml.get(eccn_upper, [])
    if not usml_hits:
        # キーワードマッチ
        itar_keywords = ["military", "defense", "munition", "warhead", "missile",
                         "radar", "sonar", "targeting", "cryptographic", "classified"]
        if any(kw in text_lower for kw in itar_keywords):
            usml_hits = ["XXI"]
    if usml_hits:
        flags.append({
            "regime": "ITAR/USML",
            "authority": "DDTC (US State Department)",
            "applicable": True,
            "categories": usml_hits,
            "note": "Commodity Jurisdiction (CJ) 確認が必要な場合があります",
        })

    # EU Dual-Use チェック
    eu_hits: list[str] = []
    if eccn_upper:
        eu_hits = eccn_to_eu.get(eccn_upper, [])
    if eu_hits:
        flags.append({
            "regime": "EU Dual-Use (2021/821)",
            "authority": "EU Member State Competent Authority",
            "applicable": True,
            "categories": eu_hits,
            "note": "EU域内輸出者はInternal Compliance Program (ICP) が推奨されます",
        })

    # MTCR チェック（ロケット・ミサイル・宇宙）
    mtcr_keywords = ["missile", "rocket", "space", "satellite", "launch vehicle",
                     "propulsion", "unmanned aerial", "uav", "drone", "warhead"]
    mtcr_eccns    = {"9A004", "9A515", "9B001", "9C001"}
    if (eccn_upper in mtcr_eccns) or any(kw in text_lower for kw in mtcr_keywords):
        flags.append({
            "regime": "MTCR (Missile Technology Control Regime)",
            "authority": "Multi-lateral (35 member states)",
            "applicable": True,
            "note": "Category I: 射程300km超/搭載量500kg超のミサイル・宇宙機。強い輸出圧力に服する。",
        })

    # NSG チェック（核関連）
    nsg_eccns = {"0A001", "0A002", "0B001", "0B002", "0C001", "0C002", "0D001", "0E001"}
    nsg_keywords = ["nuclear", "uranium", "plutonium", "centrifuge", "enrichment",
                    "reactor", "heavy water", "fissile"]
    if (eccn_upper in nsg_eccns) or any(kw in text_lower for kw in nsg_keywords):
        flags.append({
            "regime": "NSG (Nuclear Suppliers Group)",
            "authority": "Multi-lateral (48 member states)",
            "applicable": True,
            "note": "NSG Part 1/2: 核材料・設備・技術。包括的保障措置が輸出条件",
        })

    # AG チェック（化学・生物兵器前駆物質）
    ag_eccns = {"1C350", "1C351", "1C354", "2B350", "2B352"}
    ag_keywords = ["chemical weapon", "biological weapon", "precursor",
                   "nerve agent", "pathogen", "toxin", "cwc", "bwc", "sarin", "vx"]
    if (eccn_upper in ag_eccns) or any(kw in text_lower for kw in ag_keywords):
        flags.append({
            "regime": "Australia Group (AG)",
            "authority": "Multi-lateral (43 member states)",
            "applicable": True,
            "note": "化学・生物兵器前駆物質は化学兵器禁止条約（CWC）/生物兵器禁止条約（BWC）も確認",
        })

    return {
        "item_name":         body.item_name,
        "eccn":              eccn_upper or None,
        "applicable_regimes": flags,
        "total_flags":       len(flags),
        "risk_level":        "high" if flags else "none",
    }


@router.post("/changes/{change_id}/dismiss")
async def dismiss_change(change_id: int, db: AsyncSession = Depends(get_db)):
    """変更を既読（dismiss）にする。"""
    row = await db.get(RegulatoryChange, change_id)
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="change not found")
    row.is_dismissed = True
    row.dismissed_at = datetime.utcnow()
    await db.flush()
    return {"ok": True, "id": change_id}


# ── 内部: e-Gov ────────────────────────────────────────────────────────────────

async def _check_egov(db: AsyncSession) -> int:
    added = 0
    async with httpx.AsyncClient(timeout=15) as client:
        for law_id, (label, source_key) in _WATCH_LAWS.items():
            try:
                resp = await client.get(f"{_EGOV_BASE}/lawdata/{law_id}")
                resp.raise_for_status()
                data = resp.json()
                law_info = data.get("law_info") or data.get("lawInfo") or {}
                amended_at = str(
                    law_info.get("last_amendment_date")
                    or law_info.get("LastAmendmentDate", "") or ""
                )
                revision_num = str(
                    law_info.get("revision_number")
                    or law_info.get("RevisionNumber", "") or ""
                )
            except Exception as exc:
                logger.warning("e-Gov fetch failed for %s: %s", law_id, exc)
                continue

            if not amended_at:
                continue

            content_hash = _sha256(f"egov:{law_id}:{amended_at}")
            existing = await db.execute(
                select(RegulatoryChange).where(RegulatoryChange.content_hash == content_hash)
            )
            if existing.scalar_one_or_none():
                continue

            change = RegulatoryChange(
                source        = source_key,
                change_type   = "law_update",
                title         = f"【法令改正】{label} 改正（{amended_at}）",
                description   = (
                    f"e-Gov 法令APIで改正を検知しました。\n"
                    f"法令名: {label}\n最終改正日: {amended_at}\n"
                    f"リビジョン番号: {revision_num or 'N/A'}"
                ),
                severity      = "warn",
                item_ref      = law_id,
                effective_date= amended_at[:10] if amended_at else None,
                source_url    = f"https://laws.e-gov.go.jp/law/{law_id}",
                content_hash  = content_hash,
                detected_at   = datetime.utcnow(),
            )
            db.add(change)
            added += 1
            logger.info("RegMonitor: egov law update — %s (%s)", label, amended_at)

    if added:
        await db.flush()
    return added


# ── 内部: BIS Federal Register ────────────────────────────────────────────────

async def _check_bis(db: AsyncSession) -> int:
    added = 0
    params = {
        "agencies[]": _BIS_AGENCY_ID,
        "type[]":     "RULE",
        "order":      "newest",
        "per_page":   10,
        "fields[]":   ["title", "document_number", "publication_date",
                       "abstract", "html_url", "action"],
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(_FED_REGISTER_API, params=params)
            resp.raise_for_status()
            rules = resp.json().get("results", [])
    except Exception as exc:
        logger.warning("BIS FedRegister fetch failed: %s", exc)
        return 0

    for rule in rules:
        doc_no   = rule.get("document_number", "")
        title    = rule.get("title", "")
        pub_date = rule.get("publication_date", "")

        text_blob = title + " " + (rule.get("abstract") or "")
        text_lower = text_blob.lower()
        if not (
            any(kw.lower() in text_lower for kw in _EAR_KEYWORDS)
            or _EAR_WORD_RE.search(text_blob)
        ):
            continue

        content_hash = _sha256(f"bis_rule:{doc_no}")
        existing = await db.execute(
            select(RegulatoryChange).where(RegulatoryChange.content_hash == content_hash)
        )
        if existing.scalar_one_or_none():
            continue

        sev = "warn" if "entity list" in text_lower else "info"
        change = RegulatoryChange(
            source        = "bis_federal_register",
            change_type   = "new_rule",
            title         = f"【BIS新規則】{title[:200]}",
            description   = (
                f"Document No: {doc_no}\n公布日: {pub_date}\n"
                f"概要: {(rule.get('abstract') or '')[:400]}"
            ),
            severity      = sev,
            item_ref      = doc_no,
            effective_date= pub_date or None,
            source_url    = rule.get("html_url", ""),
            content_hash  = content_hash,
            detected_at   = datetime.utcnow(),
        )
        db.add(change)
        added += 1
        logger.info("RegMonitor: BIS rule — %s", doc_no)

    if added:
        await db.flush()
    return added
