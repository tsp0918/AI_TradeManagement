"""
collect/bis_entity_list.py
BIS Entity List (EL) — Trade.gov Consolidated Screening List API からの取得・パース。

API: https://api.trade.gov/consolidated_screening_list/v1/search
    ?sources=EL&size=100&offset=0&api_key=DEMO_KEY
認証: DEMO_KEY (無制限ではないが Colab 用途には十分) / 本番は Trade.gov 登録キー推奨
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

CSL_API_URL   = "https://api.trade.gov/consolidated_screening_list/v1/search"
PAGE_SIZE     = 100
RETRY_WAIT    = 3   # 秒
MAX_RETRIES   = 3


SanctionEntity = dict[str, Any]


def fetch_bis_entity_list(
    api_key: str = "DEMO_KEY",
    timeout: int = 30,
    cache_path: str | None = None,
) -> list[SanctionEntity]:
    """
    BIS Entity List を Trade.gov CSL API からページネーション取得する。

    Returns
    -------
    list of SanctionEntity dicts (list_source="bis_entity")
    """
    logger.info("Fetching BIS Entity List from Trade.gov CSL (api_key=%s)", api_key[:4] + "...")
    entities: list[SanctionEntity] = []
    offset = 0
    total  = None

    while True:
        params = {
            "sources":  "EL",
            "size":     PAGE_SIZE,
            "offset":   offset,
            "api_key":  api_key,
        }

        data = _request_with_retry(CSL_API_URL, params, timeout)
        if data is None:
            logger.error("Failed to fetch page at offset=%d", offset)
            break

        if total is None:
            total = data.get("total", 0)
            logger.info("Total EL entries: %d", total)

        results = data.get("results", [])
        if not results:
            break

        for r in results:
            entities.append(_parse_csl_result(r))

        offset += len(results)
        logger.info("Fetched %d / %d", offset, total)

        if offset >= total:
            break

        time.sleep(0.5)  # API レート制限対策

    logger.info("Total BIS Entity List entries fetched: %d", len(entities))

    if cache_path:
        import json
        from pathlib import Path
        p = Path(cache_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(entities, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Cached to %s", p)

    return entities


def _request_with_retry(url: str, params: dict, timeout: int) -> dict | None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("Attempt %d/%d failed: %s", attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_WAIT * attempt)
    return None


def _parse_csl_result(r: dict) -> SanctionEntity:
    # エイリアス
    aliases = []
    for alt in r.get("alt_names", []):
        if isinstance(alt, str) and alt:
            aliases.append(alt.strip())

    # 住所（最初の1件）
    address = ""
    addresses = r.get("addresses", [])
    if addresses:
        a = addresses[0]
        parts = [
            a.get("address", ""),
            a.get("city", ""),
            a.get("state", ""),
            a.get("postal_code", ""),
            a.get("country", ""),
        ]
        address = ", ".join(p for p in parts if p)

    # 国
    country = ""
    nationalities = r.get("nationalities", [])
    if nationalities:
        country = nationalities[0]
    elif addresses:
        country = addresses[0].get("country", "")

    return {
        "list_source":  "bis_entity",
        "entity_name":  r.get("name", "").strip(),
        "aliases":      aliases,
        "address":      address,
        "country":      country,
        "entity_type":  r.get("type", ""),
        "uid":          r.get("id", ""),
        "federal_register_notice": r.get("federal_register_notice", ""),
        "license_requirement":     r.get("license_requirement", ""),
        "license_policy":          r.get("license_policy", ""),
    }
