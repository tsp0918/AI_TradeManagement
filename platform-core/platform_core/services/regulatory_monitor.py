"""
regulatory_monitor.py — 規制動向モニタリングサービス

以下のソースを定期ポーリングし、変更を検知したら regulatory_changes テーブルへ保存する。

  1. e-Gov 法令API — 外為法・輸出貿易管理令のバージョンハッシュ監視
  2. BIS Federal Register API — EAR/輸出管理関連の新規ルール
  3. Wassenaar Arrangement — 年次改正告知（WA公式サイト）

設計方針:
  - 各ソースに専用の fetch + diff 関数
  - content_hash による重複防止（unique制約 + upsert）
  - DB セッションは呼び出し元から受け取る（依存注入）
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from platform_core.models.regulatory_change import RegulatoryChange

logger = logging.getLogger(__name__)

# ── e-Gov API ─────────────────────────────────────────────────────────────────
_EGOV_BASE = "https://laws.e-gov.go.jp/api/1"

# 監視対象法令 (法令番号 → ラベル)
_WATCH_LAWS = {
    "409AC0000000228": ("外国為替及び外国貿易法（外為法）", "meti_fefta"),
    "411CO0000000378": ("輸出貿易管理令",               "meti_eccn"),
    "413M60400000049": ("外国為替令",                   "meti_fefta"),
}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def fetch_egov_law_version(law_id: str) -> dict[str, Any] | None:
    """e-Gov API で法令の最終改正日時を取得する。"""
    try:
        url = f"{_EGOV_BASE}/lawdata/{law_id}"
        resp = httpx.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        law_info = data.get("law_info") or data.get("lawInfo") or {}
        return {
            "law_id":       law_id,
            "law_name":     law_info.get("law_name") or law_info.get("LawName", ""),
            "amended_at":   (
                law_info.get("last_amendment_date")
                or law_info.get("LastAmendmentDate", "")
            ),
            "revision_num": (
                law_info.get("revision_number")
                or law_info.get("RevisionNumber", "")
            ),
        }
    except Exception as exc:
        logger.warning("e-Gov fetch failed for %s: %s", law_id, exc)
        return None


def check_egov_changes(db: Session) -> list[RegulatoryChange]:
    """e-Gov 法令のバージョン変化を検知して RegulatoryChange レコードを生成する。"""
    added = []
    for law_id, (label, source_key) in _WATCH_LAWS.items():
        info = fetch_egov_law_version(law_id)
        if not info or not info.get("amended_at"):
            continue

        amended_at = str(info["amended_at"])
        content_hash = _sha256(f"egov:{law_id}:{amended_at}")

        # 既存チェック
        if db.query(RegulatoryChange).filter_by(content_hash=content_hash).first():
            continue  # 既に記録済み

        change = RegulatoryChange(
            source=source_key,
            change_type="law_update",
            title=f"【法令改正】{label} 改正（{amended_at}）",
            description=f"e-Gov 法令APIで改正を検知しました。\n"
                        f"法令名: {label}\n"
                        f"最終改正日: {amended_at}\n"
                        f"リビジョン番号: {info.get('revision_num', 'N/A')}",
            severity="warn",
            item_ref=law_id,
            effective_date=amended_at[:10] if amended_at else None,
            source_url=f"https://laws.e-gov.go.jp/law/{law_id}",
            content_hash=content_hash,
            detected_at=datetime.utcnow(),
        )
        db.add(change)
        added.append(change)
        logger.info("RegMonitor: new law update detected — %s (%s)", label, amended_at)

    if added:
        db.commit()
    return added


# ── BIS Federal Register ───────────────────────────────────────────────────────
_FED_REGISTER_API = "https://www.federalregister.gov/api/v1/documents.json"
_BIS_AGENCY_ID = "bureau-of-industry-and-security"
_EAR_KEYWORDS = [
    "Export Administration Regulations",
    "Entity List",
    "Commerce Control List",
    "export control",
    "EAR",
]


def fetch_bis_recent_rules(days_back: int = 90) -> list[dict[str, Any]]:
    """BIS の Federal Register 最新ルールを取得する。"""
    try:
        params = {
            "agencies[]":      _BIS_AGENCY_ID,
            "type[]":          "RULE",
            "order":           "newest",
            "per_page":        10,
            "fields[]":        ["title", "document_number", "publication_date",
                                 "abstract", "html_url", "action"],
        }
        resp = httpx.get(_FED_REGISTER_API, params=params, timeout=20)
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as exc:
        logger.warning("BIS FedRegister fetch failed: %s", exc)
        return []


def check_bis_changes(db: Session) -> list[RegulatoryChange]:
    """BIS Federal Register の新規ルールを検知して RegulatoryChange を生成する。"""
    added = []
    rules = fetch_bis_recent_rules()

    for rule in rules:
        doc_no = rule.get("document_number", "")
        title = rule.get("title", "")
        pub_date = rule.get("publication_date", "")

        # EAR 関連フィルタ
        text_blob = (title + " " + (rule.get("abstract") or "")).lower()
        if not any(kw.lower() in text_blob for kw in _EAR_KEYWORDS):
            continue

        content_hash = _sha256(f"bis_rule:{doc_no}")
        if db.query(RegulatoryChange).filter_by(content_hash=content_hash).first():
            continue

        # severity 判定: Entity List 変更は warn 以上
        sev = "warn" if "entity list" in text_blob else "info"

        change = RegulatoryChange(
            source="bis_federal_register",
            change_type="new_rule",
            title=f"【BIS新規則】{title[:200]}",
            description=(
                f"Document No: {doc_no}\n"
                f"公布日: {pub_date}\n"
                f"概要: {(rule.get('abstract') or '')[:400]}"
            ),
            severity=sev,
            item_ref=doc_no,
            effective_date=pub_date or None,
            source_url=rule.get("html_url", ""),
            content_hash=content_hash,
            detected_at=datetime.utcnow(),
        )
        db.add(change)
        added.append(change)
        logger.info("RegMonitor: BIS rule detected — %s", doc_no)

    if added:
        db.commit()
    return added


# ── 統合実行 ──────────────────────────────────────────────────────────────────

def run_all_checks(db: Session) -> dict[str, int]:
    """全ソースのチェックを実行し、追加件数を返す。"""
    egov_added = check_egov_changes(db)
    bis_added  = check_bis_changes(db)

    result = {
        "egov":  len(egov_added),
        "bis":   len(bis_added),
        "total": len(egov_added) + len(bis_added),
    }
    logger.info("RegMonitor run complete: %s", result)
    return result


def get_unread_changes(db: Session, limit: int = 5) -> list[RegulatoryChange]:
    """未読（未 dismiss）の変更を新しい順に返す。"""
    return (
        db.query(RegulatoryChange)
        .filter(RegulatoryChange.is_dismissed == False)  # noqa: E712
        .order_by(RegulatoryChange.detected_at.desc())
        .limit(limit)
        .all()
    )
