"""公式制裁リストデータ取得・同期サービス。

ソース:
  1. OFAC SDN (Specially Designated Nationals) CSV/XML    — 米国 OFAC（APIキー不要）
  2. UN Security Council Consolidated List XML            — 国連安保理（APIキー不要）
  3. EU Consolidated Financial Sanctions File (FSF)       — 欧州委員会（APIキー不要）
  4. UK OFSI Consolidated Sanctions List                  — 英国財務省 OFSI（APIキー不要）
  5. BIS Entity List (EL)                                 — 米国 BIS / Trade.gov CSL（APIキー必要）
  6. BIS Unverified List (UVL)                            — 米国 BIS / Trade.gov CSL（APIキー必要）
  7. BIS Military End-User List (MEU)                     — 米国 BIS / Trade.gov CSL（APIキー必要）
  8. BIS Denied Persons List (DPL)                        — 米国 BIS / Trade.gov CSL（APIキー必要）

自動同期（APIキー不要の3リスト）:
    fetch_ofac_sdn_csv()   → ~7,000件  (5.5MB CSV, 高速)
    fetch_un_consolidated() → ~800件   (2.2MB XML)
    fetch_eu_consolidated() → ~1,500件 (2MB XML)

usage:
    from app.services.sanctions_sync import (
        fetch_ofac_sdn, fetch_ofac_sdn_csv,
        fetch_un_consolidated,
        fetch_eu_consolidated, fetch_uk_ofsi,
        fetch_bis_entity_list,
    )
"""
from __future__ import annotations

import csv
import io
import logging
import xml.etree.ElementTree as ET
from typing import Any

import httpx

logger = logging.getLogger(__name__)

OFAC_SDN_URL     = "https://sanctionslistservice.ofac.treas.gov/api/publicationpreview/exports/sdn.xml"
OFAC_SDN_CSV_URL = "https://sanctionslistservice.ofac.treas.gov/api/publicationpreview/exports/SDN.CSV"
OFAC_SDN_URL_ALT = "https://www.treasury.gov/ofac/downloads/sdn.xml"   # 旧URL（フォールバック）
UN_SC_XML_URL    = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"
CSL_API_URL      = "https://api.trade.gov/consolidated_screening_list/v1/search"
BIS_EL_JSON_URL  = "https://efts.bis.doc.gov/complete-search-of-firm-names-and-addresses?searchOptions=allWords&keyWord=&export=true"
EU_FSF_URL       = "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content"
UK_OFSI_XML      = "https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.xml"
UK_OFSI_CSV      = "https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.csv"

_NS_SDN = "{http://tempuri.org/sdnList.xsd}"


# ── OFAC SDN ─────────────────────────────────────────────────────────────────

def fetch_ofac_sdn(timeout: int = 90) -> list[dict[str, Any]]:
    """OFAC SDN XML を取得・パースして WatchlistImportRow 相当のリストを返す。

    SDN XML は ~34MB。ストリームせず全件取得する（メモリは約 150MB 以内）。
    新URLが失敗した場合は旧URLにフォールバックする。
    """
    logger.info("Fetching OFAC SDN XML from %s", OFAC_SDN_URL)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        try:
            resp = client.get(OFAC_SDN_URL)
            resp.raise_for_status()
        except Exception as e:
            logger.warning("Primary OFAC URL failed (%s), trying fallback: %s", e, OFAC_SDN_URL_ALT)
            resp = client.get(OFAC_SDN_URL_ALT)
            resp.raise_for_status()

    root = ET.fromstring(resp.content)
    entries: list[dict[str, Any]] = []

    for entry in root.findall(f"{_NS_SDN}sdnEntry"):
        uid      = entry.findtext(f"{_NS_SDN}uid", "") or ""
        last     = entry.findtext(f"{_NS_SDN}lastName",  "") or ""
        first    = entry.findtext(f"{_NS_SDN}firstName", "") or ""
        sdn_type = entry.findtext(f"{_NS_SDN}sdnType",   "") or ""

        name = f"{first} {last}".strip() if first else last.strip()
        if not name:
            continue

        aliases: list[str] = []
        for aka in entry.findall(f"{_NS_SDN}akaList/{_NS_SDN}aka"):
            aka_last  = aka.findtext(f"{_NS_SDN}lastName",  "") or ""
            aka_first = aka.findtext(f"{_NS_SDN}firstName", "") or ""
            aka_name  = f"{aka_first} {aka_last}".strip() if aka_first else aka_last.strip()
            if aka_name and aka_name != name:
                aliases.append(aka_name)

        country: str | None = None
        for addr in entry.findall(f"{_NS_SDN}addressList/{_NS_SDN}address"):
            c = addr.findtext(f"{_NS_SDN}country", "") or ""
            if c:
                country = c[:10]
                break

        programs = [
            p.text for p in entry.findall(f"{_NS_SDN}programList/{_NS_SDN}program") if p.text
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


def fetch_ofac_sdn_csv(timeout: int = 60) -> list[dict[str, Any]]:
    """OFAC SDN を CSV から取得・パースする（XML より高速: 5.5MB vs 28MB）。

    CSV 列: Ent_num, SDN_Name, SDN_Type, Program, Title, Call_Sign,
            Vess_type, Tonnage, GRT, Vess_flag, Vess_owner, Remarks
    別名は Remarks 列の 'a.k.a.' タグを解析して抽出する。
    """
    logger.info("Fetching OFAC SDN CSV from %s", OFAC_SDN_CSV_URL)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(OFAC_SDN_CSV_URL)
        resp.raise_for_status()

    text = resp.content.decode("latin-1", errors="replace")
    reader = csv.reader(io.StringIO(text))
    entries: list[dict[str, Any]] = []

    for row in reader:
        if len(row) < 2:
            continue
        uid      = row[0].strip()
        name     = row[1].strip().strip('"')
        sdn_type = row[2].strip() if len(row) > 2 else ""
        program  = row[3].strip() if len(row) > 3 else ""
        remarks  = row[11].strip() if len(row) > 11 else ""

        if not name or name.lower() in ("-0-", ""):
            continue

        # a.k.a. 抽出（"a.k.a. 'NAME';" パターン）
        aliases: list[str] = []
        import re as _re
        for m in _re.finditer(r"a\.k\.a\.\s+'([^']+)'", remarks):
            alias = m.group(1).strip()
            if alias and alias != name:
                aliases.append(alias)

        entries.append({
            "list_source": "ofac_sdn",
            "entity_name": name,
            "aliases":     aliases or None,
            "country":     None,  # CSV版には国情報なし（XML版は住所から取得）
            "source_id":   f"OFAC-{uid}" if uid else None,
            "reason":      program or None,
            "risk_level":  "high",
            "extra":       {"sdn_type": sdn_type},
        })

    logger.info("OFAC SDN CSV: parsed %d entries", len(entries))
    return entries


# ── UN Security Council Consolidated List ────────────────────────────────────

def fetch_un_consolidated(timeout: int = 60) -> list[dict[str, Any]]:
    """国連安保理 統合制裁リスト XML を取得・パースする（APIキー不要）。

    URL: https://scsanctions.un.org/resources/xml/en/consolidated.xml
    主要タグ: CONSOLIDATED_LIST > INDIVIDUALS/INDIVIDUAL + ENTITIES/ENTITY
    """
    logger.info("Fetching UN SC Consolidated List from %s", UN_SC_XML_URL)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(UN_SC_XML_URL)
        resp.raise_for_status()

    root = ET.fromstring(resp.content)
    entries: list[dict[str, Any]] = []

    def _parse_un_entity(el: ET.Element, entity_type: str) -> dict[str, Any] | None:
        ref_num  = (el.findtext("REFERENCE_NUMBER") or "").strip()
        un_list  = (el.findtext("UN_LIST_TYPE") or "").strip()
        comments = (el.findtext("COMMENTS1") or "").strip()

        if entity_type == "INDIVIDUAL":
            first  = (el.findtext("FIRST_NAME")  or "").strip()
            second = (el.findtext("SECOND_NAME") or "").strip()
            third  = (el.findtext("THIRD_NAME")  or "").strip()
            fourth = (el.findtext("FOURTH_NAME") or "").strip()
            parts  = [p for p in [first, second, third, fourth] if p]
            name   = " ".join(parts)
        else:
            name = (el.findtext("FIRST_NAME") or el.findtext("ENTITY_NAME") or "").strip()

        if not name:
            return None

        aliases: list[str] = []
        for alias_section in ("INDIVIDUAL_ALIAS", "ENTITY_ALIAS"):
            for alias_el in el.findall(f"{alias_section}"):
                q    = (alias_el.findtext("QUALITY") or "").strip()
                aname = (alias_el.findtext("ALIAS_NAME") or "").strip()
                if aname and aname != name and q.lower() != "low":
                    aliases.append(aname)

        country: str | None = None
        for addr_el in el.findall("INDIVIDUAL_ADDRESS") or el.findall("ENTITY_ADDRESS"):
            c = (addr_el.findtext("COUNTRY") or "").strip()
            if c:
                country = c[:10]
                break

        return {
            "list_source": "un_sc",
            "entity_name": name,
            "aliases":     aliases or None,
            "country":     country,
            "source_id":   f"UN-{ref_num}" if ref_num else None,
            "reason":      f"{un_list} {comments}".strip() or None,
            "risk_level":  "high",
            "extra":       {"un_list_type": un_list, "entity_type": entity_type},
        }

    for indiv in root.iter("INDIVIDUAL"):
        e = _parse_un_entity(indiv, "INDIVIDUAL")
        if e:
            entries.append(e)

    for entity in root.iter("ENTITY"):
        e = _parse_un_entity(entity, "ENTITY")
        if e:
            entries.append(e)

    logger.info("UN SC Consolidated: parsed %d entries", len(entries))
    return entries


# ── Trade.gov Consolidated Screening List API (汎用) ─────────────────────────

def _fetch_csl_source(
    source_code: str,
    list_source_key: str,
    api_key: str = "DEMO_KEY",
    timeout: int = 30,
) -> list[dict[str, Any]]:
    """Trade.gov CSL API から指定ソースを全件取得する汎用関数。

    Args:
        source_code:     CSL APIの sources パラメータ（"EL", "UVL", "MEU", "DPL" 等）
        list_source_key: DB に保存する list_source 値
    """
    logger.info("Fetching Trade.gov CSL source=%s (key=%s...)", source_code, api_key[:8])

    entries: list[dict[str, Any]] = []
    offset    = 0
    page_size = 100

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        while True:
            params = {
                "sources": source_code,
                "size":    page_size,
                "offset":  offset,
                "api_key": api_key,
            }
            resp = client.get(CSL_API_URL, params=params)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Trade.gov CSL API returned HTTP {resp.status_code}. "
                    "DEMO_KEY has limited access — register at api.trade.gov for a free key."
                )
            if not resp.text.strip():
                raise RuntimeError("Trade.gov CSL API returned empty response (rate limit or key issue)")
            data = resp.json()

            results: list[dict] = data.get("results", [])
            if not results:
                break

            for r in results:
                name = (r.get("name") or "").strip()
                if not name:
                    continue

                aliases: list[str] = r.get("alt_names") or []

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
                    "list_source": list_source_key,
                    "entity_name": name,
                    "aliases":     aliases or None,
                    "address":     "; ".join(address_parts) if address_parts else None,
                    "country":     country,
                    "source_id":   str(source_id)[:200] if source_id else None,
                    "reason":      reason,
                    "risk_level":  "high",
                    "extra": {
                        "source_code":              source_code,
                        "federal_register_notice":  r.get("federal_register_notice"),
                        "start_date":               r.get("start_date"),
                    },
                })

            offset += len(results)
            total = data.get("total", 0)
            logger.debug("CSL %s: fetched %d / %d", source_code, offset, total)
            if offset >= total:
                break

    logger.info("CSL %s: fetched %d entries", source_code, len(entries))
    return entries


def fetch_bis_entity_list(api_key: str = "DEMO_KEY", timeout: int = 30) -> list[dict[str, Any]]:
    """BIS Entity List を Trade.gov CSL API から全件取得する。"""
    return _fetch_csl_source("EL", "bis_entity", api_key=api_key, timeout=timeout)


def fetch_bis_unverified(api_key: str = "DEMO_KEY", timeout: int = 30) -> list[dict[str, Any]]:
    """BIS Unverified List を Trade.gov CSL API から全件取得する。"""
    return _fetch_csl_source("UVL", "bis_uvl", api_key=api_key, timeout=timeout)


def fetch_bis_meu(api_key: str = "DEMO_KEY", timeout: int = 30) -> list[dict[str, Any]]:
    """BIS Military End-User List を Trade.gov CSL API から全件取得する。"""
    return _fetch_csl_source("MEU", "bis_meu", api_key=api_key, timeout=timeout)


def fetch_bis_dpl(api_key: str = "DEMO_KEY", timeout: int = 30) -> list[dict[str, Any]]:
    """BIS Denied Persons List を Trade.gov CSL API から全件取得する。"""
    return _fetch_csl_source("DPL", "bis_dpl", api_key=api_key, timeout=timeout)


# ── EU Consolidated Financial Sanctions File (FSF) ──────────────────────────

def fetch_eu_consolidated(timeout: int = 90) -> list[dict[str, Any]]:
    """EU 統合制裁リスト（FSF XML）を取得・パースする。

    欧州委員会 DG FISMA が管理する XML フォーマット。
    URL: https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content
    """
    logger.info("Fetching EU Consolidated Sanctions from %s", EU_FSF_URL)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(EU_FSF_URL)
        resp.raise_for_status()

    root = ET.fromstring(resp.content)
    entries: list[dict[str, Any]] = []

    # EU XML には名前空間が含まれる場合があるため、動的に処理
    ns_prefix = ""
    if root.tag.startswith("{"):
        ns_uri   = root.tag.split("}")[0][1:]
        ns_prefix = f"{{{ns_uri}}}"

    for entity in root.findall(f"{ns_prefix}sanctionEntity"):
        # 主名称（wholeName または firstName + lastName）
        primary_name: str | None = None
        aliases: list[str] = []

        for na in entity.findall(f"{ns_prefix}nameAlias"):
            whole = (na.get("wholeName") or "").strip()
            first = (na.get("firstName") or "").strip()
            last  = (na.get("lastName")  or "").strip()
            composed = f"{first} {last}".strip() if first else last

            candidate = whole or composed
            if not candidate:
                continue

            quality = (na.get("quality") or "").lower()
            if quality in ("good", "a.k.a.", "") and primary_name is None:
                primary_name = candidate
            elif candidate != primary_name:
                aliases.append(candidate)

        if not primary_name:
            continue

        # 国（住所リストの先頭）
        country: str | None = None
        for addr in entity.findall(f"{ns_prefix}addresses/{ns_prefix}address"):
            c = (addr.get("countryDescription") or addr.get("countryIso2Code") or "").strip()
            if c:
                country = c[:10]
                break

        # 制裁プログラム
        reg_el = entity.find(f"{ns_prefix}regulation")
        programme = (reg_el.get("programme") or "") if reg_el is not None else ""
        number    = (reg_el.get("numberTitle") or "") if reg_el is not None else ""
        reason    = f"{programme} {number}".strip() or None

        # エンティティID
        entity_id = entity.get("eu-reference-number") or entity.get("logicalId") or ""

        entries.append({
            "list_source": "eu_consolidated",
            "entity_name": primary_name,
            "aliases":     aliases or None,
            "country":     country,
            "source_id":   f"EU-{entity_id}" if entity_id else None,
            "reason":      reason,
            "risk_level":  "high",
            "extra":       {"programme": programme},
        })

    logger.info("EU Consolidated: parsed %d entries", len(entries))
    return entries


# ── UK OFSI Consolidated Sanctions List ─────────────────────────────────────

def fetch_uk_ofsi(timeout: int = 60) -> list[dict[str, Any]]:
    """UK OFSI 統合制裁リストを XML または CSV から取得・パースする。

    XML は Azure Blob Storage から取得。失敗時は CSV にフォールバック。
    """
    try:
        return _fetch_uk_ofsi_xml(timeout=timeout)
    except Exception as e:
        logger.warning("UK OFSI XML failed (%s), falling back to CSV", e)
        return _fetch_uk_ofsi_csv(timeout=timeout)


def _fetch_uk_ofsi_xml(timeout: int = 60) -> list[dict[str, Any]]:
    logger.info("Fetching UK OFSI XML from %s", UK_OFSI_XML)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(UK_OFSI_XML)
        resp.raise_for_status()

    root = ET.fromstring(resp.content)
    entries: list[dict[str, Any]] = []

    # ArrayOfDesignation > Designation
    for desig in root.findall(".//Designation"):
        group_id    = (desig.findtext("GroupID") or "").strip()
        sanctions_list = (desig.findtext("UKSanctionsList") or "").strip()

        # 名前
        primary_name: str | None = None
        aliases: list[str] = []
        for name_el in desig.findall(".//Name"):
            # Name6 = whole name, Name1-5 = parts
            whole  = (name_el.findtext("Name6") or "").strip()
            first  = (name_el.findtext("Name1") or "").strip()
            middle = " ".join(filter(None, [
                name_el.findtext("Name2") or "",
                name_el.findtext("Name3") or "",
                name_el.findtext("Name4") or "",
            ])).strip()
            last   = (name_el.findtext("Name5") or "").strip()

            parts   = [p for p in [first, middle, last] if p]
            composed = " ".join(parts) or whole
            candidate = whole or composed
            if not candidate:
                continue
            if primary_name is None:
                primary_name = candidate
            elif candidate != primary_name:
                aliases.append(candidate)

        if not primary_name:
            continue

        # 国
        country: str | None = None
        for addr in desig.findall(".//Address"):
            c = (addr.findtext("AddressCountry") or "").strip()
            if c:
                country = c[:10]
                break

        entries.append({
            "list_source": "uk_ofsi",
            "entity_name": primary_name,
            "aliases":     aliases or None,
            "country":     country,
            "source_id":   f"UK-{group_id}" if group_id else None,
            "reason":      sanctions_list or None,
            "risk_level":  "high",
            "extra":       {"sanctions_list": sanctions_list},
        })

    logger.info("UK OFSI XML: parsed %d entries", len(entries))
    return entries


def _fetch_uk_ofsi_csv(timeout: int = 60) -> list[dict[str, Any]]:
    logger.info("Fetching UK OFSI CSV from %s", UK_OFSI_CSV)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(UK_OFSI_CSV)
        resp.raise_for_status()

    text = resp.content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for row in reader:
        name   = (row.get("Name 6") or row.get("Entity Name") or "").strip()
        if not name:
            # 姓名から合成
            parts = [
                row.get("Name 1") or "",
                row.get("Name 2") or "",
                row.get("Name 3") or "",
                row.get("Name 4") or "",
                row.get("Name 5") or "",
            ]
            name = " ".join(p.strip() for p in parts if p.strip())
        if not name:
            continue

        group_id = (row.get("Group ID") or "").strip()
        if group_id and group_id in seen_ids:
            continue
        if group_id:
            seen_ids.add(group_id)

        country = (row.get("Country") or row.get("Address 6") or "")[:10] or None
        sanctions_list = (row.get("Sanctions List") or row.get("UK Sanctions List") or "").strip()

        entries.append({
            "list_source": "uk_ofsi",
            "entity_name": name,
            "aliases":     None,
            "country":     country or None,
            "source_id":   f"UK-{group_id}" if group_id else None,
            "reason":      sanctions_list or None,
            "risk_level":  "high",
            "extra":       {"sanctions_list": sanctions_list},
        })

    logger.info("UK OFSI CSV: parsed %d entries", len(entries))
    return entries
