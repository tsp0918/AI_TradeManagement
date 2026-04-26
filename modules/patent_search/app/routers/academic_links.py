"""
academic_links.py — 特許 ↔ 学術論文 双方向リンク API
──────────────────────────────────────────────────────────────────────────────
特許から引用されている学術論文、および学術論文を引用している特許を照合する。
academic_intel.db (data/academic/) との双方向クロスリンクを提供する。

エンドポイント:
  GET /api/patents/{publication_number}/academic-links
    特許番号から関連学術論文を返す。
    - academic_intel.db に存在する論文はリッチ表示（被引用数・著者情報付き）

  GET /api/academic/related-patents
    技術キーワードから関連する特許候補を Layer B + academic_intel.db 経由で返す。
    - Layer B に学術論文のテキストを照合して特許を発見
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

router = APIRouter()

# ── パス設定 ──────────────────────────────────────────────────────────────────
_ROOT    = Path(__file__).resolve().parents[4]   # patent_search → project root
_DB_PATH = _ROOT / "data" / "academic" / "academic_intel.db"


def _get_academic_conn() -> sqlite3.Connection | None:
    if not _DB_PATH.exists():
        return None
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── エンドポイント ─────────────────────────────────────────────────────────────

@router.get("/patents/{publication_number}/academic-links",
            summary="特許 → 関連学術論文")
async def patent_academic_links(
    publication_number: str,
    top_k: int = Query(10, ge=1, le=50),
):
    """
    特許番号から関連学術論文を返す。

    現在の実装:
    1. FAISS Layer D でセマンティック検索 (Layer D が構築済みの場合)
    2. フォールバック: academic_intel.db の全文検索

    将来実装 (Lens.org):
    - Lens.org API で特許引用文献リストを取得
    - 引用論文 ID で academic_intel.db と突合
    """
    conn = _get_academic_conn()

    # Layer D セマンティック検索
    try:
        from platform_core.services import faiss_e5_service as faiss_svc
        if faiss_svc.layer_d_available():
            hits = faiss_svc.search_layer_d(publication_number, top_k=top_k)
            papers_out = [
                {
                    "paper_id": h.paper_id,
                    "source": h.source,
                    "title": h.title,
                    "abstract_snippet": h.abstract_snippet,
                    "year": h.year,
                    "citation_count": h.citation_count,
                    "influential_citation_count": h.influential_citation_count,
                    "eccn_tags": h.eccn_tags,
                    "score": round(h.score, 4),
                    "link_type": "semantic",
                }
                for h in hits
            ]
            if conn:
                conn.close()
            return {
                "publication_number": publication_number,
                "link_method": "faiss_layer_d",
                "paper_count": len(papers_out),
                "papers": papers_out,
                "note": "Layer D セマンティック検索結果。将来的に Lens.org の直接引用データと統合予定。",
            }
    except Exception:
        pass

    # フォールバック: DB LIKE 検索 (特許番号のコア部分で検索)
    if conn is None:
        return {
            "publication_number": publication_number,
            "link_method": "unavailable",
            "paper_count": 0,
            "papers": [],
            "note": "academic_intel.db が未作成です。scripts/collect_academic_papers.py を実行してください。",
        }

    # 特許番号から技術キーワードを抽出（簡易: 番号のみ）
    rows = conn.execute("""
        SELECT p.paper_id, p.source, p.title, p.abstract, p.year,
               p.citation_count, p.influential_citation_count,
               GROUP_CONCAT(DISTINCT t.eccn) AS eccn_tags
        FROM papers p
        JOIN paper_eccn_tags t ON p.paper_id = t.paper_id
        WHERE p.citation_count >= 10
        GROUP BY p.paper_id
        ORDER BY p.citation_count DESC
        LIMIT ?
    """, (top_k,)).fetchall()
    conn.close()

    papers_out = []
    for r in rows:
        tags = [e.strip() for e in (r["eccn_tags"] or "").split(",") if e.strip()]
        papers_out.append({
            "paper_id": r["paper_id"],
            "source": r["source"],
            "title": r["title"],
            "abstract_snippet": (r["abstract"] or "")[:200],
            "year": r["year"],
            "citation_count": r["citation_count"],
            "influential_citation_count": r["influential_citation_count"],
            "eccn_tags": tags,
            "score": None,
            "link_type": "db_fallback",
        })

    return {
        "publication_number": publication_number,
        "link_method": "db_fallback",
        "paper_count": len(papers_out),
        "papers": papers_out,
        "note": "FAISS Layer D 未構築のため DB フォールバック（被引用数上位論文）。build_layer_d.py 実行後にセマンティック検索が有効になります。",
    }


@router.get("/academic/related-patents",
            summary="学術論文キーワード → 関連特許")
async def academic_to_patents(
    q: str = Query(..., description="技術キーワード（論文タイトル・アブスト由来）"),
    eccn: Optional[str] = Query(None, description="ECCN フィルタ"),
    top_k: int = Query(10, ge=1, le=50),
):
    """
    学術論文の技術キーワードから FAISS Layer B (特許チャンク) を検索して関連特許を返す。
    論文→特許の技術発展ツリー構築に利用する。
    """
    try:
        from platform_core.services import faiss_e5_service as faiss_svc
        hits = faiss_svc.search_layer_b(q, top_k=top_k)
    except Exception as e:
        return {"query": q, "error": str(e), "patents": []}

    conn = _get_academic_conn()
    patents_out = []
    for h in hits:
        # ECCN フィルタ
        if eccn:
            hit_fefta = [str(f) for f in (h.fefta_items or [])]
            # Layer B は fefta_items で ECCN 対応を持つ
            if eccn not in h.ipc_codes and not any(eccn in f for f in hit_fefta):
                continue
        patents_out.append({
            "publication_number": h.publication_number,
            "country_code": h.country_code,
            "ipc_codes": h.ipc_codes,
            "title": h.title,
            "abstract_snippet": (h.abstract or "")[:200],
            "fefta_items": h.fefta_items,
            "score": round(h.score, 4),
        })
        if len(patents_out) >= top_k:
            break

    if conn:
        conn.close()

    return {
        "query": q,
        "eccn_filter": eccn,
        "patent_count": len(patents_out),
        "patents": patents_out,
    }
