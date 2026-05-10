#!/usr/bin/env python3
"""
collect_academic_papers.py
──────────────────────────────────────────────────────────────────────────────
Semantic Scholar Academic Graph API + Lens.org API を使い、
ECCN別技術用語辞書 (data/academic/eccn_tech_terms.json) から
関連論文メタデータをバッチ収集して academic_intel.db (SQLite) にキャッシュする。

使い方:
  # 全ECCN収集 (初回・夜間バッチ想定)
  python scripts/collect_academic_papers.py --all --limit 200

  # 特定ECCNのみ
  python scripts/collect_academic_papers.py --eccn 3A001 --limit 100

  # 差分更新 (直近30日に出版された論文のみ)
  python scripts/collect_academic_papers.py --all --limit 50 --recent-days 30

  # Lens.org のみ使う
  python scripts/collect_academic_papers.py --eccn 5A002 --source lens

  # 著者詳細を追加取得 (h-index等)
  python scripts/collect_academic_papers.py --enrich-authors

API仕様:
  Semantic Scholar:
    - 無料 API Key (要登録): https://www.semanticscholar.org/product/api
    - API Key なし: ~1 req/sec / Key あり: 100 req/sec
    - .env に SEMANTIC_SCHOLAR_API_KEY=xxx で設定

  Lens.org:
    - 非商用無料 (要登録): https://www.lens.org/lens/user/subscriptions
    - .env に LENS_ORG_API_KEY=xxx で設定
    - ライセンス: CC BY 4.0 (非商用) / 商用は別途プラン要
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

# ── パス設定 ─────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
TERMS_PATH = PROJECT_ROOT / "data" / "academic" / "eccn_tech_terms.json"
DB_PATH = PROJECT_ROOT / "data" / "academic" / "academic_intel.db"

# ── API 設定 ──────────────────────────────────────────────────────────────────
S2_BASE = "https://api.semanticscholar.org/graph/v1"
LENS_BASE = "https://api.lens.org/scholarly/search"

S2_PAPER_FIELDS = (
    "paperId,title,abstract,year,citationCount,"
    "influentialCitationCount,authors,externalIds,fieldsOfStudy,publicationDate"
)
S2_AUTHOR_FIELDS = "authorId,name,hIndex,affiliations,papers.year,papers.citationCount"

# ── DB 初期化 ─────────────────────────────────────────────────────────────────
def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS papers (
        paper_id       TEXT PRIMARY KEY,
        source         TEXT NOT NULL DEFAULT 's2',  -- 's2' or 'lens'
        title          TEXT,
        abstract       TEXT,
        year           INTEGER,
        citation_count INTEGER DEFAULT 0,
        influential_citation_count INTEGER DEFAULT 0,
        fields_of_study TEXT,   -- JSON array
        external_ids   TEXT,    -- JSON {"DOI":..., "MAG":..., "ArXiv":...}
        publication_date TEXT,
        fetched_at     TEXT
    );

    CREATE TABLE IF NOT EXISTS authors (
        author_id   TEXT PRIMARY KEY,
        source      TEXT NOT NULL DEFAULT 's2',
        name        TEXT,
        h_index     INTEGER,
        affiliations TEXT,  -- JSON array of {name, country_code}
        fetched_at  TEXT
    );

    CREATE TABLE IF NOT EXISTS paper_authors (
        paper_id    TEXT,
        author_id   TEXT,
        author_name TEXT,
        PRIMARY KEY (paper_id, author_id)
    );

    CREATE TABLE IF NOT EXISTS paper_eccn_tags (
        paper_id        TEXT,
        eccn            TEXT,
        keyword_matched TEXT,
        PRIMARY KEY (paper_id, eccn)
    );

    CREATE INDEX IF NOT EXISTS idx_tags_eccn ON paper_eccn_tags(eccn);
    CREATE INDEX IF NOT EXISTS idx_papers_year ON papers(year);
    CREATE INDEX IF NOT EXISTS idx_papers_citation ON papers(citation_count DESC);
    CREATE INDEX IF NOT EXISTS idx_papers_source ON papers(source);
    """)
    conn.commit()


# ── ユーティリティ ────────────────────────────────────────────────────────────
def _load_terms() -> dict:
    data = json.loads(TERMS_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _get_api_key(name: str) -> str:
    """環境変数から API Key を取得（未設定でも動作する）"""
    import os
    return os.getenv(name, "")


def _sleep(sec: float) -> None:
    time.sleep(sec)


# ── Semantic Scholar ──────────────────────────────────────────────────────────
def s2_search(query: str, limit: int = 100, api_key: str = "",
              year_from: int | None = None) -> list[dict]:
    """論文をキーワード検索して最大 limit 件返す。"""
    headers = {"x-api-key": api_key} if api_key else {}
    params: dict = {
        "query": query,
        "limit": min(limit, 100),
        "fields": S2_PAPER_FIELDS,
    }
    if year_from:
        params["year"] = f"{year_from}-"

    try:
        resp = httpx.get(
            f"{S2_BASE}/paper/search",
            params=params,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])
    except httpx.HTTPStatusError as e:
        print(f"    [S2 WARN] HTTP {e.response.status_code} for '{query}'", file=sys.stderr)
        return []
    except Exception as e:
        print(f"    [S2 WARN] {e}", file=sys.stderr)
        return []


def s2_author_detail(author_id: str, api_key: str = "") -> dict | None:
    """著者詳細 (h-index, 所属) を取得する。"""
    headers = {"x-api-key": api_key} if api_key else {}
    try:
        resp = httpx.get(
            f"{S2_BASE}/author/{author_id}",
            params={"fields": S2_AUTHOR_FIELDS},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"    [S2 author WARN] {author_id}: {e}", file=sys.stderr)
        return None


# ── Lens.org ──────────────────────────────────────────────────────────────────
def lens_search(query: str, limit: int = 100, api_key: str = "",
                year_from: int | None = None) -> list[dict]:
    """Lens.org Scholarly Search API で論文を検索する。"""
    if not api_key:
        print("    [Lens SKIP] LENS_ORG_API_KEY が未設定のためスキップ", file=sys.stderr)
        return []

    must_clauses: list[dict] = [
        {"match": {"title": query}},
    ]
    if year_from:
        must_clauses.append({"range": {"year_published": {"gte": year_from}}})

    payload = {
        "query": {"bool": {"must": must_clauses}},
        "size": min(limit, 100),
        "include": [
            "lens_id", "title", "abstract", "year_published",
            "citation_count", "scholarly_citations_count",
            "authors", "external_ids", "fields_of_study",
        ],
        "sort": [{"citation_count": "desc"}],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        resp = httpx.post(LENS_BASE, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json().get("data", [])
    except httpx.HTTPStatusError as e:
        print(f"    [Lens WARN] HTTP {e.response.status_code} for '{query}'", file=sys.stderr)
        return []
    except Exception as e:
        print(f"    [Lens WARN] {e}", file=sys.stderr)
        return []


# ── DB 書き込み ────────────────────────────────────────────────────────────────
def upsert_s2_paper(conn: sqlite3.Connection, paper: dict,
                     eccn: str, keyword: str) -> None:
    now = datetime.utcnow().isoformat()
    conn.execute(
        """INSERT OR REPLACE INTO papers
           (paper_id, source, title, abstract, year, citation_count,
            influential_citation_count, fields_of_study, external_ids,
            publication_date, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            paper["paperId"], "s2",
            paper.get("title"), paper.get("abstract"),
            paper.get("year"), paper.get("citationCount", 0),
            paper.get("influentialCitationCount", 0),
            json.dumps(paper.get("fieldsOfStudy") or []),
            json.dumps(paper.get("externalIds") or {}),
            paper.get("publicationDate"),
            now,
        ),
    )
    conn.execute(
        "INSERT OR IGNORE INTO paper_eccn_tags VALUES (?,?,?)",
        (paper["paperId"], eccn, keyword),
    )
    for a in paper.get("authors") or []:
        if not a.get("authorId"):
            continue
        conn.execute(
            "INSERT OR IGNORE INTO paper_authors VALUES (?,?,?)",
            (paper["paperId"], a["authorId"], a.get("name")),
        )


def upsert_lens_paper(conn: sqlite3.Connection, paper: dict,
                       eccn: str, keyword: str) -> None:
    now = datetime.utcnow().isoformat()
    lens_id = paper.get("lens_id", "")
    if not lens_id:
        return
    paper_id = f"lens:{lens_id}"

    # external_ids を統一フォーマットに変換
    ext_raw = paper.get("external_ids") or []
    external_ids = {e.get("type", ""): e.get("value", "") for e in ext_raw if isinstance(e, dict)}

    conn.execute(
        """INSERT OR REPLACE INTO papers
           (paper_id, source, title, abstract, year, citation_count,
            influential_citation_count, fields_of_study, external_ids,
            publication_date, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            paper_id, "lens",
            paper.get("title"), paper.get("abstract"),
            paper.get("year_published"),
            paper.get("citation_count", 0),
            paper.get("scholarly_citations_count", 0),
            json.dumps(paper.get("fields_of_study") or []),
            json.dumps(external_ids),
            None,
            now,
        ),
    )
    conn.execute(
        "INSERT OR IGNORE INTO paper_eccn_tags VALUES (?,?,?)",
        (paper_id, eccn, keyword),
    )
    for a in paper.get("authors") or []:
        if not isinstance(a, dict):
            continue
        author_id = f"lens:author:{a.get('first_name','')}{a.get('last_name','')}".replace(" ", "_")
        conn.execute(
            "INSERT OR IGNORE INTO paper_authors VALUES (?,?,?)",
            (paper_id, author_id, f"{a.get('first_name','')} {a.get('last_name','')}".strip()),
        )


def upsert_author(conn: sqlite3.Connection, author_data: dict) -> None:
    now = datetime.utcnow().isoformat()
    affiliations = []
    for aff in author_data.get("affiliations") or []:
        affiliations.append({"name": aff.get("name", ""), "country_code": ""})
    conn.execute(
        """INSERT OR REPLACE INTO authors
           (author_id, source, name, h_index, affiliations, fetched_at)
           VALUES (?,?,?,?,?,?)""",
        (
            author_data["authorId"], "s2",
            author_data.get("name"),
            author_data.get("hIndex"),
            json.dumps(affiliations),
            now,
        ),
    )


# ── メイン収集処理 ─────────────────────────────────────────────────────────────
def collect_eccn(
    eccn: str,
    entry: dict,
    conn: sqlite3.Connection,
    limit: int,
    source: str,
    recent_days: int | None,
    s2_key: str,
    lens_key: str,
) -> int:
    keywords = entry.get("keywords", [])
    year_from = None
    if recent_days:
        year_from = (datetime.utcnow() - timedelta(days=recent_days)).year

    total_new = 0
    rate_sleep = 2.5 if not s2_key else 0.05  # API key なしは 2.5s（429対策）

    for kw in keywords:
        # Semantic Scholar
        if source in ("s2", "both"):
            papers = s2_search(kw, limit=limit, api_key=s2_key, year_from=year_from)
            new_in_kw = 0
            for p in papers:
                if not p.get("abstract") or not p.get("paperId"):
                    continue
                upsert_s2_paper(conn, p, eccn, kw)
                new_in_kw += 1
            conn.commit()
            print(f"    [S2]   {eccn} / '{kw}' → {new_in_kw} 件")
            _sleep(rate_sleep)

        # Lens.org
        if source in ("lens", "both"):
            papers_l = lens_search(kw, limit=limit, api_key=lens_key, year_from=year_from)
            new_lens = 0
            for p in papers_l:
                if not p.get("abstract"):
                    continue
                upsert_lens_paper(conn, p, eccn, kw)
                new_lens += 1
            conn.commit()
            if source != "s2":
                print(f"    [Lens] {eccn} / '{kw}' → {new_lens} 件")
            _sleep(0.5)

        total_new += 1  # keyword processed

    return total_new


def enrich_authors(conn: sqlite3.Connection, s2_key: str, limit: int = 500) -> None:
    """paper_authors テーブルから未取得著者の詳細を Semantic Scholar から補完する。"""
    rows = conn.execute("""
        SELECT DISTINCT pa.author_id
        FROM paper_authors pa
        LEFT JOIN authors a ON pa.author_id = a.author_id
        WHERE a.author_id IS NULL
          AND pa.author_id NOT LIKE 'lens:%'
        LIMIT ?
    """, (limit,)).fetchall()

    print(f"\n著者詳細取得: {len(rows)} 件")
    rate_sleep = 1.1 if not s2_key else 0.05
    for (author_id,) in rows:
        data = s2_author_detail(author_id, api_key=s2_key)
        if data and data.get("authorId"):
            upsert_author(conn, data)
            conn.commit()
        _sleep(rate_sleep)
    print("著者詳細取得 完了")


# ── CLI ───────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Semantic Scholar + Lens.org から学術論文を収集して academic_intel.db に保存"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="全ECCNを収集")
    group.add_argument("--eccn", metavar="CODE", help="特定のECCNコードのみ (例: 3A001)")
    group.add_argument("--enrich-authors", action="store_true", help="著者詳細を補完取得")

    parser.add_argument("--limit", type=int, default=100,
                        help="キーワードあたり最大取得件数 (default: 100)")
    parser.add_argument("--source", choices=["s2", "lens", "both"], default="both",
                        help="データソース (default: both)")
    parser.add_argument("--recent-days", type=int, default=None,
                        help="指定日数以内に出版された論文のみ取得")
    args = parser.parse_args()

    # API Keys
    s2_key = _get_api_key("SEMANTIC_SCHOLAR_API_KEY")
    # Lens.org は LENS_ORG_API_KEY または Scholarly_Works_API を受け付ける
    lens_key = _get_api_key("LENS_ORG_API_KEY") or _get_api_key("Scholarly_Works_API")

    if s2_key:
        print(f"Semantic Scholar API Key: 設定済み")
    else:
        print("Semantic Scholar API Key: 未設定 (レート制限: ~1 req/sec)")

    if lens_key:
        print(f"Lens.org API Key: 設定済み")
    else:
        print("Lens.org API Key: 未設定 (Lens収集はスキップ)")

    # DB 初期化
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    terms = _load_terms()

    if args.enrich_authors:
        enrich_authors(conn, s2_key=s2_key)
        conn.close()
        return

    # 収集対象を決定
    if args.all:
        targets = list(terms.items())
    else:
        if args.eccn not in terms:
            print(f"ERROR: ECCN '{args.eccn}' は eccn_tech_terms.json に見つかりません")
            print(f"  利用可能: {', '.join(terms.keys())}")
            sys.exit(1)
        targets = [(args.eccn, terms[args.eccn])]

    print(f"\n収集開始: {len(targets)} ECCN / limit={args.limit} / source={args.source}")
    if args.recent_days:
        print(f"  直近 {args.recent_days} 日以内の論文のみ")
    print()

    for i, (eccn, entry) in enumerate(targets, 1):
        print(f"[{i}/{len(targets)}] {eccn} — {entry.get('label', '')}")
        collect_eccn(
            eccn=eccn,
            entry=entry,
            conn=conn,
            limit=args.limit,
            source=args.source,
            recent_days=args.recent_days,
            s2_key=s2_key,
            lens_key=lens_key,
        )

    # 統計表示
    total_papers = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    total_s2 = conn.execute("SELECT COUNT(*) FROM papers WHERE source='s2'").fetchone()[0]
    total_lens = conn.execute("SELECT COUNT(*) FROM papers WHERE source='lens'").fetchone()[0]
    total_tags = conn.execute("SELECT COUNT(DISTINCT eccn) FROM paper_eccn_tags").fetchone()[0]

    print(f"\n{'='*50}")
    print(f"収集完了")
    print(f"  総論文数: {total_papers} 件 (S2: {total_s2}, Lens: {total_lens})")
    print(f"  ECCNタグ数: {total_tags} 種")
    print(f"  DB: {DB_PATH}")
    print(f"{'='*50}")

    conn.close()


if __name__ == "__main__":
    main()
