"""
J-PlatPat フォールバック検索サービス。

BigQuery が未設定の場合に J-PlatPat OA REST API（inpit.go.jp）へフォールバックする。
公開エンドポイント: https://jppatsearch.inpit.go.jp/api/v1/simpleSearch
（認証不要のシンプル検索）
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

_JPLATPAT_API = "https://jppatsearch.inpit.go.jp/api/v1/simpleSearch"
_TIMEOUT = 15.0


async def search_jplatpat(
    keywords: str,
    inventor: Optional[str] = None,
    applicant: Optional[str] = None,
    limit: int = 20,
) -> Dict:
    """
    J-PlatPat シンプル検索 API を呼び出す。

    Parameters
    ----------
    keywords : str
        検索キーワード（スペース区切りで AND 検索）
    inventor : str, optional
        発明者名フィルタ（J-PlatPat はクエリに追加）
    applicant : str, optional
        出願人名フィルタ
    limit : int
        最大結果件数 (max 50)

    Returns
    -------
    dict
        {"results": [...], "count": int, "query": str, "source": "jplatpat"}
    """
    query_parts = [keywords]
    if inventor:
        query_parts.append(f"発明者:{inventor}")
    if applicant:
        query_parts.append(f"出願人:{applicant}")

    query = " ".join(query_parts)
    params = {
        "q":    query,
        "type": "P",       # 特許のみ
        "lang": "JA",
        "rows": min(limit, 50),
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(_JPLATPAT_API, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning("J-PlatPat HTTP %s: %s", exc.response.status_code, exc)
        return {"results": [], "count": 0, "query": keywords, "source": "jplatpat",
                "error": f"J-PlatPat API エラー (HTTP {exc.response.status_code})"}
    except Exception as exc:
        logger.warning("J-PlatPat 接続失敗: %s", exc)
        return {"results": [], "count": 0, "query": keywords, "source": "jplatpat",
                "error": f"J-PlatPat 接続失敗: {exc}"}

    patents: List[Dict] = []
    for item in data.get("result", data.get("results", [])):
        pub_no = item.get("publicationNumber") or item.get("publication_number", "")
        patents.append({
            "source":             "jplatpat",
            "publication_number": pub_no,
            "title":              item.get("title") or item.get("inventionTitle", "タイトル不明"),
            "abstract":           item.get("abstract") or "",
            "description":        "",
            "filing_date":        _fmt_date(item.get("applicationDate") or item.get("filingDate")),
            "publication_date":   _fmt_date(item.get("publicationDate")),
            "inventors":          _extract_names(item.get("inventors") or item.get("inventor")),
            "assignees":          _extract_names(item.get("applicants") or item.get("applicant")),
            "country_code":       (pub_no[:2] if pub_no else "JP"),
            "url":                (
                f"https://www.j-platpat.inpit.go.jp/c1800/PU/JP-{pub_no}/ja"
                if pub_no else ""
            ),
        })

    return {"results": patents, "count": len(patents), "query": keywords, "source": "jplatpat"}


def _fmt_date(value) -> Optional[str]:
    if not value:
        return None
    s = str(value).replace("/", "-").replace(".", "-")
    return s[:10] if len(s) >= 8 else s


def _extract_names(value) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        names = []
        for v in value:
            if isinstance(v, str):
                names.append(v)
            elif isinstance(v, dict):
                names.append(v.get("name") or v.get("fullName") or "")
        return [n for n in names if n]
    return []
