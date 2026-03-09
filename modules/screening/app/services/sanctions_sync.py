"""公式制裁リストデータ取得・同期サービス。

ソース:
  1. OFAC SDN (Specially Designated Nationals) XML
     URL: https://www.treasury.gov/ofac/downloads/sdn.xml
     認証不要・公開
  2. BIS Entity List
     URL: Trade.gov Consolidated Screening List API (DEMO_KEY 使用)
     認証不要 (DEMO_KEY は 30req/h の制限あり)

usage:
    from app.services.sanctions_sync import fetch_ofac_sdn, fetch_bis_entity_list
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any

import httpx

logger = logging.getLogger(__name__)

OFAC_SDN_URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"
CSL_API_URL  = "https://api.trade.gov/consolidated_screening_list/v1/search"

# OFAC SDN XML の名前空間
_NS = "{http://tempuri.org/sdnList.xsd}"


# ── OFAC SDN ─────────────────────────────────────────────────────────────────

def fetch_ofac_sdn(timeout: int = 90) -> list[dict[str, Any]]:
    """OFAC SDN XML を取得・パースして WatchlistImportRow 相当のリストを返す。

    SDN XML は ~34MB。ストリームせず全件取得する（メモリは約 150MB 以内）。
    """
    logger.info("Fetching OFAC SDN XML from %s", OFAC_SDN_URL)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(OFAC_SDN_URL)
        resp.raise_for_status()

    root = ET.fromstring(resp.content)
    entries: list[dict[str, Any]] = []

    for entry in root.findall(f"{_NS}sdnEntry"):
        uid      = entry.findtext(f"{_NS}uid", "") or ""
        last     = entry.findtext(f"{_NS}lastName",  "") or ""
        first    = entry.findtext(f"{_NS}firstName", "") or ""
        sdn_type = entry.findtext(f"{_NS}sdnType",   "") or ""

        name = f"{first} {last}".strip() if first else last.strip()
        if not name:
            continue

        # エイリアス
        aliases: list[str] = []
        for aka in entry.findall(f"{_NS}akaList/{_NS}aka"):
            aka_last  = aka.findtext(f"{_NS}lastName",  "") or ""
            aka_first = aka.findtext(f"{_NS}firstName", "") or ""
            aka_name  = f"{aka_first} {aka_last}".strip() if aka_first else aka_last.strip()
            if aka_name and aka_name != name:
                aliases.append(aka_name)

        # 国（住所リストの先頭）
        country: str | None = None
        for addr in entry.findall(f"{_NS}addressList/{_NS}address"):
            c = addr.findtext(f"{_NS}country", "") or ""
            if c:
                country = c[:10]
                break

        # 制裁プログラム（理由）
        programs = [
            p.text for p in entry.findall(f"{_NS}programList/{_NS}program") if p.text
        ]
        reason = ", ".join(programs) if programs else None

        entries.append({
            "list_source": "ofac_sdn",
            "entity_name": name,
            "aliases":     aliases or None,
            "country":     country,
            "source_id":   f"OFAC-{uid}",
            "reason":      reason,
            "risk_level":  "high",
            "extra":       {"sdn_type": sdn_type},
        })

    logger.info("OFAC SDN: parsed %d entries", len(entries))
    return entries


# ── BIS Entity List (Trade.gov Consolidated Screening List API) ──────────────

def fetch_bis_entity_list(
    api_key: str = "DEMO_KEY",
    timeout: int = 30,
) -> list[dict[str, Any]]:
    """Trade.gov CSL API から BIS Entity List を全件取得する。

    DEMO_KEY は 30 req/h まで。EL は通常 ~1,700 件なので 17ページ = 17リクエスト。
    """
    logger.info("Fetching BIS Entity List from Trade.gov CSL API (key=%s)", api_key[:8])

    entries: list[dict[str, Any]] = []
    offset    = 0
    page_size = 100

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        while True:
            params = {
                "sources": "EL",
                "size":    page_size,
                "offset":  offset,
                "api_key": api_key,
            }
            resp = client.get(CSL_API_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

            results: list[dict] = data.get("results", [])
            if not results:
                break

            for r in results:
                name = (r.get("name") or "").strip()
                if not name:
                    continue

                aliases: list[str] = r.get("alt_names") or []

                # 住所・国
                country:       str | None = None
                address_parts: list[str]  = []
                for addr in (r.get("addresses") or []):
                    c = (addr.get("country") or "")
                    if c and not country:
                        country = c[:10]
                    parts = [
                        addr.get("address", ""),
                        addr.get("city", ""),
                        addr.get("postal_code", ""),
                        c,
                    ]
                    address_parts.append(", ".join(p for p in parts if p))

                source_id = (r.get("id") or r.get("source_list_url") or "")
                reason    = (r.get("license_policy") or r.get("license_requirement") or None)

                entries.append({
                    "list_source": "bis_entity",
                    "entity_name": name,
                    "aliases":     aliases or None,
                    "address":     "; ".join(address_parts) if address_parts else None,
                    "country":     country,
                    "source_id":   str(source_id)[:200] if source_id else None,
                    "reason":      reason,
                    "risk_level":  "high",
                    "extra": {
                        "federal_register_notice": r.get("federal_register_notice"),
                        "start_date":              r.get("start_date"),
                    },
                })

            offset += len(results)
            total = data.get("total", 0)
            logger.debug("BIS EL: fetched %d / %d", offset, total)
            if offset >= total:
                break

    logger.info("BIS Entity List: fetched %d entries", len(entries))
    return entries
