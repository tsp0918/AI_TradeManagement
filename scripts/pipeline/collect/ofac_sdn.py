"""
collect/ofac_sdn.py
OFAC SDN List (Specially Designated Nationals) 公式XMLフィードの取得・パース。

公式URL: https://www.treasury.gov/ofac/downloads/sdn.xml (~34MB, 認証不要)
出力形式: List[SanctionEntity] (WatchlistImportRow 互換 dict)
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import requests

logger = logging.getLogger(__name__)

OFAC_SDN_URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"
_NS = "{http://tempuri.org/sdnList.xsd}"

# ── 型定義 ───────────────────────────────────────────────────────────────────

SanctionEntity = dict[str, Any]  # list_source, entity_name, aliases, address, country, ...


# ── フェッチ ─────────────────────────────────────────────────────────────────

def fetch_ofac_sdn(
    url: str = OFAC_SDN_URL,
    timeout: int = 90,
    cache_path: Path | None = None,
) -> list[SanctionEntity]:
    """
    OFAC SDN XML を取得してパースする。

    Parameters
    ----------
    url         : 取得先URL（デフォルト: 公式）
    timeout     : HTTP タイムアウト秒
    cache_path  : 指定するとキャッシュJSONに保存する

    Returns
    -------
    list of SanctionEntity dicts  (最大 ~13,000 件)
    """
    logger.info("Fetching OFAC SDN from %s", url)
    t0 = time.time()

    resp = requests.get(url, timeout=timeout, stream=True)
    resp.raise_for_status()

    raw_xml = resp.content
    logger.info("Downloaded %.1f MB in %.1fs", len(raw_xml) / 1e6, time.time() - t0)

    entities = _parse_sdn_xml(raw_xml)
    logger.info("Parsed %d SDN entries", len(entities))

    if cache_path:
        import json
        cache_path = Path(cache_path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(entities, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("Cached to %s", cache_path)

    return entities


def _parse_sdn_xml(raw_xml: bytes) -> list[SanctionEntity]:
    root = ET.fromstring(raw_xml)

    # 名前空間なしで再試行するフォールバック付きパーサー
    ns = _NS if root.tag.startswith("{") else ""
    tag = lambda name: f"{ns}{name}" if ns else name  # noqa: E731

    entities: list[SanctionEntity] = []

    for entry in root.iter(tag("sdnEntry")):
        uid_el      = entry.find(tag("uid"))
        first_el    = entry.find(tag("firstName"))
        last_el     = entry.find(tag("lastName"))
        sdn_type_el = entry.find(tag("sdnType"))

        if last_el is None:
            continue

        # 氏名・組織名
        last  = (last_el.text or "").strip()
        first = (first_el.text or "").strip() if first_el is not None else ""
        entity_name = f"{first} {last}".strip() if first else last

        # エイリアス
        aliases: list[str] = []
        for aka in entry.iter(tag("aka")):
            aka_last  = aka.find(tag("lastName"))
            aka_first = aka.find(tag("firstName"))
            if aka_last is not None and aka_last.text:
                parts = [aka_last.text.strip()]
                if aka_first is not None and aka_first.text:
                    parts.insert(0, aka_first.text.strip())
                aliases.append(" ".join(parts))

        # 住所（最初の1件）
        address = ""
        addr_el = entry.find(f".//{tag('address')}")
        if addr_el is not None:
            parts = []
            for sub in ("address1", "city", "country"):
                el = addr_el.find(tag(sub))
                if el is not None and el.text:
                    parts.append(el.text.strip())
            address = ", ".join(parts)

        # 国籍（最初の1件）
        country = ""
        nationality_el = entry.find(f".//{tag('nationality')}")
        if nationality_el is not None:
            country_el = nationality_el.find(tag("country"))
            if country_el is not None and country_el.text:
                country = country_el.text.strip()
        if not country and addr_el is not None:
            c_el = addr_el.find(tag("country"))
            if c_el is not None and c_el.text:
                country = c_el.text.strip()

        entities.append(
            {
                "list_source":   "ofac_sdn",
                "entity_name":   entity_name,
                "aliases":       aliases,
                "address":       address,
                "country":       country,
                "entity_type":   (sdn_type_el.text or "").strip() if sdn_type_el is not None else "",
                "uid":           (uid_el.text or "").strip() if uid_el is not None else "",
            }
        )

    return entities
