"""
fetch_patents_lens.py
──────────────────────────────────────────────────────────────────────────────
Lens.org Patent Search API から特許データを取得し、
Layer B 統合用 JSON（data/staging/lens_patents_raw.json）に保存するスクリプト。

Lens.org は JP / US / EP / CN / AU 等の多国特許を統一 API で提供。
IPC コード検索・キーワード検索・出願人検索が可能。

API ドキュメント: https://docs.api.lens.org/patent.html
API トークン取得: https://www.lens.org/lens/user/subscriptions

環境変数（.env）:
  LENS_PATENT_API_KEY   Lens.org Patent API の Bearer トークン
  Scholarly_Works_API   (兼用可) Lens.org の Bearer トークン

使い方:
  python scripts/fetch_patents_lens.py [--limit 200] [--country JP,US,EP] [--dry-run]

出力:
  data/staging/lens_patents_raw.json   → merge_lens_patents_to_layer_b.py で Layer B に統合
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_ROOT    = Path(__file__).resolve().parents[1]
_STAGING = _ROOT / "data" / "staging"
_OUT     = _STAGING / "lens_patents_raw.json"

_LENS_PATENT_API = "https://api.lens.org/patent/search"
_LENS_SCHOLARLY_API = "https://api.lens.org/scholarly/search"
_TIMEOUT = 30.0
_DEFAULT_LIMIT = 100  # per IPC code

# 輸出管理関連 IPC コード（ECCN との対応が高い上位コード）
_TARGET_IPC_CODES = [
    "H01L",   # 半導体デバイス (3A001/3B001/3C001)
    "H04L",   # デジタル通信 (5A001/5A002)
    "H04K",   # 秘密通信・ジャミング (5A001)
    "G01S",   # 位置・速度検出（ソナー/レーダー）(6A001/6A002)
    "G01J",   # 光測定・赤外線 (6A002)
    "G01C",   # 測定・航法 (7A001/7A002)
    "G21C",   # 原子炉 (0A001/0C001)
    "G21F",   # 放射線防護 (0A001)
    "C06B",   # 爆発物・推進剤 (1C111)
    "D01F",   # 化学繊維（炭素繊維）(1C010)
    "B23B",   # 旋盤 (2B001)
    "B24B",   # 研削 (2B001)
    "B64C",   # 航空機 (9A610/9E001)
    "B64G",   # 宇宙機 (9A004)
    "F02K",   # ジェット推進 (9A001)
    "H03F",   # 増幅器（マイクロ波/GaN）(3A001)
    "H01Q",   # アンテナ (5A001)
    "C23C",   # 金属被覆（半導体製造）(3B001)
    "G06F",   # デジタル計算 (4A003)
    "C01G",   # 核物質 (0C001)
]


def _get_api_key() -> str:
    """環境変数から Lens.org API キーを取得する。"""
    key = (
        os.environ.get("LENS_PATENT_API_KEY") or
        os.environ.get("LENS_ORG_API_KEY") or
        os.environ.get("Scholarly_Works_API") or
        ""
    )
    return key


def _search_patents(
    token: str,
    ipc_prefix: str,
    country_codes: list[str],
    limit: int,
    offset: int = 0,
) -> dict:
    """Lens.org Patent Search API で IPC コードで特許を検索する。"""
    # IPC コード前方一致: query_string を全文検索として使用（field指定なし）
    must_clauses: list[dict] = [
        {"query_string": {"query": f"{ipc_prefix}*"}},
        {"term": {"kind": "A"}},   # 出願のみ（タイトル/アブスト付き）
    ]

    # 国コードフィルター
    filter_clauses: list[dict] = [
        {"range": {"date_published": {"gte": "2015-01-01"}}},
    ]
    if country_codes:
        filter_clauses.append({"terms": {"jurisdiction": country_codes}})

    query: dict = {"bool": {"must": must_clauses, "filter": filter_clauses}}

    payload = {
        "query": query,
        "size": min(limit, 100),
        "from": offset,
        "include": [
            "lens_id",
            "jurisdiction",
            "doc_number",
            "kind",
            "date_published",
            "abstract",
            "biblio",
        ],
        "sort": [{"date_published": "desc"}],
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    resp = httpx.post(
        _LENS_PATENT_API,
        json=payload,
        headers=headers,
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _normalize_patent(raw: dict, ipc_prefix: str) -> dict | None:
    """Lens.org レスポンスを Layer B 統合形式に正規化する。"""
    pub_no = raw.get("doc_number") or raw.get("lens_id", "")
    if not pub_no:
        return None

    jurisdiction = raw.get("jurisdiction", "")
    biblio = raw.get("biblio", {})

    # タイトルを取得 biblio.invention_title: [{"text": "...", "lang": "ja"}]
    title_list = biblio.get("invention_title", [])
    if isinstance(title_list, list) and title_list:
        title = next(
            (t.get("text", "") for t in title_list if t.get("lang", "").lower() in ("ja", "en")),
            title_list[0].get("text", "")
        )
    else:
        title = ""

    # アブストラクト abstract: [{"text": "...", "lang": "ja"}]（top-level）
    abstract_list = raw.get("abstract", [])
    if isinstance(abstract_list, list) and abstract_list:
        abstract = next(
            (a.get("text", "") for a in abstract_list if a.get("lang", "").lower() in ("ja", "en")),
            abstract_list[0].get("text", "")
        )
    else:
        abstract = ""

    # IPC コード biblio.classifications_ipcr.classifications[].symbol
    ipcr = biblio.get("classifications_ipcr", {}).get("classifications", [])
    ipc_codes = ",".join(c.get("symbol", "") for c in ipcr if c.get("symbol"))

    filing_date = raw.get("date_published", "")

    return {
        "source_type":        "patent",
        "source_name":        "lens_org",
        "publication_number": pub_no,
        "country_code":       jurisdiction,
        "ipc_codes":          ipc_codes[:200],
        "title":              title[:300],
        "abstract":           abstract[:600],
        "filing_date":        str(filing_date),
        "fefta_items":        [],
        "has_fefta_mapping":  False,
        "_ipc_prefix_searched": ipc_prefix,
    }


def fetch(
    ipc_list: list[str],
    country_codes: list[str],
    limit_per_ipc: int,
    dry_run: bool,
) -> None:
    token = _get_api_key()
    if not token:
        logger.error(
            "Lens.org API キーが未設定です。\n"
            "  1. https://www.lens.org/lens/user/subscriptions でトークンを取得\n"
            "  2. .env に LENS_PATENT_API_KEY=<token> を設定してください"
        )
        return

    logger.info("Lens.org Patent API で特許取得開始")
    logger.info("対象国: %s", country_codes or "全国")
    logger.info("対象 IPC: %d コード", len(ipc_list))

    if dry_run:
        # API 接続テスト (1件だけ)
        try:
            result = _search_patents(token, ipc_list[0], country_codes, limit=1)
            total = result.get("total", 0) if isinstance(result.get("total"), int) else result.get("total", {}).get("value", 0)
            logger.info("[dry-run] IPC=%s → API 応答 OK (total=%d)", ipc_list[0], total)
        except httpx.HTTPStatusError as e:
            logger.error("API エラー HTTP %d: %s", e.response.status_code, e.response.text[:200])
        return

    all_patents: list[dict] = []
    seen_pub_nos: set[str] = set()

    for idx, ipc in enumerate(ipc_list, 1):
        logger.info("[%d/%d] IPC=%s (limit=%d)", idx, len(ipc_list), ipc, limit_per_ipc)
        try:
            result = _search_patents(token, ipc, country_codes, limit=limit_per_ipc)
            hits = result.get("data", [])
            total = result.get("total", 0) if isinstance(result.get("total"), int) else result.get("total", {}).get("value", 0)
            logger.info("  total=%d, fetched=%d", total, len(hits))

            for raw in hits:
                normalized = _normalize_patent(raw, ipc)
                if normalized and normalized["publication_number"] not in seen_pub_nos:
                    seen_pub_nos.add(normalized["publication_number"])
                    all_patents.append(normalized)

            time.sleep(6.5)  # Lens.org: 10 req/min → 6秒インターバル
        except httpx.HTTPStatusError as e:
            logger.warning("  IPC=%s: HTTP %d %s", ipc, e.response.status_code, e.response.text[:100])
        except Exception as e:
            logger.warning("  IPC=%s: %s", ipc, e)

    logger.info("取得完了: %d件（重複除去後）", len(all_patents))

    # IPC別・国別集計
    ipc_counts: dict[str, int] = {}
    country_counts: dict[str, int] = {}
    for p in all_patents:
        pfx = p.get("_ipc_prefix_searched", "?")
        ipc_counts[pfx] = ipc_counts.get(pfx, 0) + 1
        cc = p.get("country_code", "?")
        country_counts[cc] = country_counts.get(cc, 0) + 1

    logger.info("IPC別: %s", sorted(ipc_counts.items(), key=lambda x: -x[1])[:10])
    logger.info("国別: %s", sorted(country_counts.items(), key=lambda x: -x[1])[:10])

    _STAGING.mkdir(parents=True, exist_ok=True)
    out = {
        "total": len(all_patents),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "lens_org",
        "ipc_list": ipc_list,
        "country_codes": country_codes,
        "ipc_counts": ipc_counts,
        "country_counts": country_counts,
        "patents": all_patents,
    }
    _OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("保存: %s (%d件)", _OUT, len(all_patents))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Lens.org Patent API から特許を取得して Layer B 統合用 JSON に保存"
    )
    parser.add_argument(
        "--limit", type=int, default=_DEFAULT_LIMIT,
        help=f"IPC コードあたり最大取得件数 (default: {_DEFAULT_LIMIT})"
    )
    parser.add_argument(
        "--country", type=str, default="JP",
        help="国コードをカンマ区切りで指定 (default: JP, 例: JP,US,EP)"
    )
    parser.add_argument(
        "--ipc", type=str, default="",
        help=f"IPC コードをカンマ区切りで指定 (default: 全 {len(_TARGET_IPC_CODES)} コード)"
    )
    parser.add_argument("--dry-run", action="store_true", help="接続テストのみ実行")
    args = parser.parse_args()

    ipc_list = (
        [x.strip() for x in args.ipc.split(",") if x.strip()]
        if args.ipc else _TARGET_IPC_CODES
    )
    country_codes = [x.strip() for x in args.country.split(",") if x.strip()]

    fetch(
        ipc_list=ipc_list,
        country_codes=country_codes,
        limit_per_ipc=args.limit,
        dry_run=args.dry_run,
    )
