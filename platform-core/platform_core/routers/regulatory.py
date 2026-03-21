"""
regulatory.py — 規制動向モニタリング API

エンドポイント:
  GET  /api/regulatory/changes          未読変更一覧
  POST /api/regulatory/check            モニタリング実行（外部API ポーリング）
  POST /api/regulatory/changes/{id}/dismiss  既読マーク
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.db.session import get_db
from platform_core.models.regulatory_change import RegulatoryChange

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/regulatory", tags=["regulatory"])

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
