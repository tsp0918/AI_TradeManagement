"""
academic_intel.py — 技術インテリジェンス API エンドポイント
──────────────────────────────────────────────────────────────────────────────
academic_intel.db (Semantic Scholar + Lens.org) のデータを提供する。

エンドポイント一覧:
  GET  /api/v1/academic/search          — FAISS Layer D セマンティック検索
  GET  /api/v1/academic/papers          — DB 直接検索 (ECCN / year / keyword)
  GET  /api/v1/academic/trend/{eccn}    — ECCN 別年次トレンド
  GET  /api/v1/academic/eccn-list       — 利用可能 ECCN 一覧
  GET  /api/v1/academic/researcher/{id} — 著者プロファイル
  POST /api/v1/academic/deemed-scan     — みなし輸出リスクスキャン
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()

# ── パス設定 ──────────────────────────────────────────────────────────────────
_ROOT   = Path(__file__).resolve().parents[6]   # rnd_assessment モジュールルート→ project root
_DB_PATH = _ROOT / "data" / "academic" / "academic_intel.db"
_TERMS_PATH = _ROOT / "data" / "academic" / "eccn_tech_terms.json"


def _get_conn() -> sqlite3.Connection:
    if not _DB_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail="academic_intel.db が未作成です。scripts/collect_academic_papers.py を実行してください。",
        )
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _load_terms() -> dict:
    if not _TERMS_PATH.exists():
        return {}
    data = json.loads(_TERMS_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


# ── Pydantic モデル ────────────────────────────────────────────────────────────
class PaperOut(BaseModel):
    paper_id: str
    source: str
    title: str | None
    abstract_snippet: str | None
    year: int | None
    citation_count: int
    influential_citation_count: int
    eccn_tags: list[str]
    score: float | None = None   # セマンティック検索スコア（DB検索時は None）


class TrendPoint(BaseModel):
    year: int
    paper_count: int
    total_citations: int
    avg_citations: float


class AuthorOut(BaseModel):
    author_id: str
    name: str | None
    h_index: int | None
    affiliations: list[dict]
    paper_count: int
    papers: list[dict]


class DeemedScanRequest(BaseModel):
    eccn_codes: list[str]           # 対象 ECCN コード
    researcher_countries: list[str] # 研究者・共著者の国コード (例: ["CN", "RU"])
    top_k: int = 10


# ── ヘルパー ──────────────────────────────────────────────────────────────────
def _rows_to_papers(rows: list) -> list[PaperOut]:
    result = []
    for r in rows:
        tags_raw = r["eccn_tags"] if "eccn_tags" in r.keys() else ""
        tags = [t.strip() for t in (tags_raw or "").split(",") if t.strip()]
        abstract = (r.get("abstract") or "")[:200]
        result.append(PaperOut(
            paper_id=r["paper_id"],
            source=r.get("source", "s2"),
            title=r.get("title"),
            abstract_snippet=abstract,
            year=r.get("year"),
            citation_count=r.get("citation_count", 0),
            influential_citation_count=r.get("influential_citation_count", 0),
            eccn_tags=tags,
        ))
    return result


# ── エンドポイント ─────────────────────────────────────────────────────────────

@router.get("/search", summary="FAISS Layer D セマンティック検索")
def semantic_search(
    q: str = Query(..., description="検索クエリ（技術説明・キーワード）"),
    eccn: Optional[str] = Query(None, description="ECCN フィルタ (例: 3A001)"),
    top_k: int = Query(20, ge=1, le=100),
):
    """FAISS Layer D を使ったセマンティック検索。Layer D が未構築の場合は DB フォールバック。"""
    try:
        from platform_core.services import faiss_e5_service as faiss_svc
        if faiss_svc.layer_d_available():
            eccn_filter = [eccn] if eccn else None
            hits = faiss_svc.search_layer_d(q, top_k=top_k, eccn_filter=eccn_filter)
            return {
                "source": "faiss_layer_d",
                "query": q,
                "total": len(hits),
                "papers": [
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
                    }
                    for h in hits
                ],
            }
    except Exception:
        pass

    # Layer D 未構築の場合は DB LIKE 検索でフォールバック
    return papers(q=q, eccn=eccn, top_k=top_k)


@router.get("/papers", summary="DB 直接検索")
def papers(
    q: Optional[str] = Query(None, description="タイトル・アブストラクトのキーワード"),
    eccn: Optional[str] = Query(None, description="ECCN フィルタ (例: 3A001)"),
    year_from: Optional[int] = Query(None, description="出版年下限"),
    year_to: Optional[int] = Query(None, description="出版年上限"),
    min_citations: int = Query(0, ge=0),
    top_k: int = Query(50, ge=1, le=500),
    order_by: str = Query("citation_count", description="ソート項目: citation_count / year / influential_citation_count"),
):
    conn = _get_conn()
    where_clauses = [
        "p.abstract IS NOT NULL",
        "p.citation_count >= :min_citations",
    ]
    params: dict = {"min_citations": min_citations, "top_k": top_k}

    if q:
        where_clauses.append("(p.title LIKE :q OR p.abstract LIKE :q)")
        params["q"] = f"%{q}%"
    if year_from:
        where_clauses.append("p.year >= :year_from")
        params["year_from"] = year_from
    if year_to:
        where_clauses.append("p.year <= :year_to")
        params["year_to"] = year_to
    if eccn:
        where_clauses.append("t.eccn = :eccn")
        params["eccn"] = eccn

    allowed_order = {"citation_count", "year", "influential_citation_count"}
    sort_col = order_by if order_by in allowed_order else "citation_count"

    sql = f"""
        SELECT
            p.paper_id, p.source, p.title, p.abstract, p.year,
            p.citation_count, p.influential_citation_count,
            GROUP_CONCAT(DISTINCT t.eccn) AS eccn_tags
        FROM papers p
        JOIN paper_eccn_tags t ON p.paper_id = t.paper_id
        WHERE {" AND ".join(where_clauses)}
        GROUP BY p.paper_id
        ORDER BY p.{sort_col} DESC
        LIMIT :top_k
    """
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    result = _rows_to_papers(rows)
    return {"source": "db", "total": len(result), "papers": [p.model_dump() for p in result]}


@router.get("/trend/{eccn}", summary="ECCN 別年次トレンド")
def trend(eccn: str):
    """指定 ECCN の年別論文数・被引用数推移を返す。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT
            p.year,
            COUNT(DISTINCT p.paper_id) AS paper_count,
            SUM(p.citation_count) AS total_citations,
            AVG(p.citation_count) AS avg_citations
        FROM papers p
        JOIN paper_eccn_tags t ON p.paper_id = t.paper_id
        WHERE t.eccn = ?
          AND p.year IS NOT NULL
          AND p.year >= 2000
        GROUP BY p.year
        ORDER BY p.year
    """, (eccn,)).fetchall()
    conn.close()

    terms = _load_terms()
    label = terms.get(eccn, {}).get("label", eccn)

    return {
        "eccn": eccn,
        "label": label,
        "trend": [
            TrendPoint(
                year=r["year"],
                paper_count=r["paper_count"],
                total_citations=r["total_citations"] or 0,
                avg_citations=round(r["avg_citations"] or 0, 1),
            ).model_dump()
            for r in rows
        ],
    }


@router.get("/eccn-list", summary="利用可能 ECCN 一覧")
def eccn_list():
    """DB に論文が存在する ECCN コードと件数を返す。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT t.eccn, COUNT(DISTINCT t.paper_id) AS paper_count
        FROM paper_eccn_tags t
        GROUP BY t.eccn
        ORDER BY paper_count DESC
    """).fetchall()
    conn.close()

    terms = _load_terms()
    return {
        "eccn_list": [
            {
                "eccn": r["eccn"],
                "label": terms.get(r["eccn"], {}).get("label", ""),
                "paper_count": r["paper_count"],
            }
            for r in rows
        ]
    }


@router.get("/researcher/{author_id}", summary="著者プロファイル")
def researcher(author_id: str):
    """著者の h-index・所属・論文リストを返す。"""
    conn = _get_conn()
    author = conn.execute(
        "SELECT * FROM authors WHERE author_id = ?", (author_id,)
    ).fetchone()

    if not author:
        raise HTTPException(status_code=404, detail=f"著者 {author_id} が見つかりません")

    # 著者の論文一覧 (被引用数降順)
    paper_rows = conn.execute("""
        SELECT p.paper_id, p.title, p.year, p.citation_count,
               GROUP_CONCAT(DISTINCT t.eccn) AS eccn_tags
        FROM paper_authors pa
        JOIN papers p ON pa.paper_id = p.paper_id
        LEFT JOIN paper_eccn_tags t ON p.paper_id = t.paper_id
        WHERE pa.author_id = ?
        GROUP BY p.paper_id
        ORDER BY p.citation_count DESC
        LIMIT 20
    """, (author_id,)).fetchall()

    conn.close()

    affiliations = json.loads(author["affiliations"] or "[]")
    papers_out = [
        {
            "paper_id": r["paper_id"],
            "title": r["title"],
            "year": r["year"],
            "citation_count": r["citation_count"],
            "eccn_tags": [e.strip() for e in (r["eccn_tags"] or "").split(",") if e.strip()],
        }
        for r in paper_rows
    ]

    return AuthorOut(
        author_id=author["author_id"],
        name=author["name"],
        h_index=author["h_index"],
        affiliations=affiliations,
        paper_count=len(papers_out),
        papers=papers_out,
    ).model_dump()


@router.post("/deemed-scan", summary="みなし輸出リスクスキャン")
def deemed_scan(req: DeemedScanRequest):
    """
    指定 ECCN × 研究者国籍の組み合わせから、みなし輸出リスクを評価する。
    リスクの高い著者・論文のリストを返す。

    判定ロジック:
    - 対象 ECCN の論文著者の所属国が risk_countries (懸念国) に含まれる場合 → 高リスク
    - 著者の h-index が高いほどリスク重みを加算
    """
    # 懸念国リスト (EAR Country Chart の NS 規制が厳しい国)
    HIGH_RISK_COUNTRIES = {"CN", "RU", "KP", "IR", "SY", "CU", "VE"}
    target_countries = set(c.upper() for c in req.researcher_countries)
    all_risk_countries = HIGH_RISK_COUNTRIES | target_countries

    conn = _get_conn()
    results = []

    for eccn in req.eccn_codes:
        # ECCN 対応論文の著者を取得
        rows = conn.execute("""
            SELECT
                pa.author_id, pa.author_name,
                a.h_index, a.affiliations,
                p.paper_id, p.title, p.year, p.citation_count
            FROM paper_eccn_tags t
            JOIN papers p ON t.paper_id = p.paper_id
            JOIN paper_authors pa ON p.paper_id = pa.paper_id
            LEFT JOIN authors a ON pa.author_id = a.author_id
            WHERE t.eccn = ?
              AND p.citation_count >= 5
            ORDER BY p.citation_count DESC
            LIMIT ?
        """, (eccn, req.top_k * 3)).fetchall()

        for r in rows:
            affiliations = json.loads(r["affiliations"] or "[]")
            author_countries = {
                (aff.get("country_code") or "").upper()
                for aff in affiliations
                if aff.get("country_code")
            }
            flagged = author_countries & all_risk_countries
            if not flagged:
                continue

            h_index = r["h_index"] or 0
            risk_score = min(100, 50 + h_index * 2 + len(flagged) * 10)

            results.append({
                "eccn": eccn,
                "author_id": r["author_id"],
                "author_name": r["author_name"],
                "h_index": h_index,
                "flagged_countries": list(flagged),
                "paper_id": r["paper_id"],
                "paper_title": r["title"],
                "paper_year": r["year"],
                "citation_count": r["citation_count"],
                "risk_score": risk_score,
                "risk_level": "high" if risk_score >= 70 else "medium",
            })
            if len(results) >= req.top_k:
                break

    conn.close()
    results.sort(key=lambda x: -x["risk_score"])

    return {
        "scanned_eccns": req.eccn_codes,
        "researcher_countries": req.researcher_countries,
        "flagged_count": len(results),
        "results": results[:req.top_k],
    }
