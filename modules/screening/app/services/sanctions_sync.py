"""公式制裁リストデータ取得・同期サービス。

ソース:
  1. OFAC SDN (Specially Designated Nationals) CSV/XML    — 米国 OFAC（APIキー不要）
  2. UN Security Council Consolidated List XML            — 国連安保理（APIキー不要）
  3. EU Consolidated Financial Sanctions File (FSF)       — 欧州委員会（APIキー不要）
  4. UK OFSI Consolidated Sanctions List                  — 英国財務省 OFSI（APIキー不要）
  5. BIS Entity List (EL)                                 — eCFR 15 CFR Part 744 Suppl. 4（APIキー不要・全件）
  6. BIS Unverified List (UVL)                            — 米国 BIS / Trade.gov CSL（APIキー必要）
  7. BIS Military End-User List (MEU)                     — 米国 BIS / Trade.gov CSL（APIキー必要）
  8. BIS Denied Persons List (DPL)                        — 米国 BIS / Trade.gov CSL（APIキー必要）

自動同期（APIキー不要）:
    fetch_ofac_sdn_csv()        → ~19,000件  (5.5MB CSV)
    fetch_un_consolidated()     → ~1,000件   (XML)
    fetch_bis_entity_list_ecfr() → ~3,420件  (eCFR XML, APIキー不要, 全件)
    fetch_uk_ofsi()             → ~6,500件   (CSV, 16MB)
"""
from __future__ import annotations

import csv
import html as html_module
import io
import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

import httpx

logger = logging.getLogger(__name__)

OFAC_SDN_URL     = "https://sanctionslistservice.ofac.treas.gov/api/publicationpreview/exports/sdn.xml"
OFAC_SDN_CSV_URL = "https://sanctionslistservice.ofac.treas.gov/api/publicationpreview/exports/SDN.CSV"
OFAC_SDN_URL_ALT = "https://www.treasury.gov/ofac/downloads/sdn.xml"   # 旧URL（フォールバック）
UN_SC_XML_URL    = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"
CSL_API_URL      = "https://data.trade.gov/consolidated_screening_list/v1/search"
ECFR_PART744_URL = "https://www.ecfr.gov/api/versioner/v1/full/{date}/title-15.xml?subchapter=C&part=744"
ECFR_VERSIONS_URL = "https://www.ecfr.gov/api/versioner/v1/versions/title-15.json"
EU_FSF_URL       = "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content"
UK_OFSI_XML      = "https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.xml"
UK_OFSI_CSV      = "https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.csv"

_NS_SDN = "{https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/XML}"
_NS_SDN_ALT = "{http://tempuri.org/sdnList.xsd}"  # 旧名前空間（フォールバック）


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

    # 名前空間を動的判定（新URL では名前空間が変更されている）
    ns = _NS_SDN
    if not root.findall(f"{ns}sdnEntry"):
        ns = _NS_SDN_ALT
        logger.info("OFAC SDN: using alt namespace %s", ns)

    for entry in root.findall(f"{ns}sdnEntry"):
        uid      = entry.findtext(f"{ns}uid", "") or ""
        last     = entry.findtext(f"{ns}lastName",  "") or ""
        first    = entry.findtext(f"{ns}firstName", "") or ""
        sdn_type = entry.findtext(f"{ns}sdnType",   "") or ""

        name = f"{first} {last}".strip() if first else last.strip()
        if not name:
            continue

        aliases: list[str] = []
        for aka in entry.findall(f"{ns}akaList/{ns}aka"):
            aka_last  = aka.findtext(f"{ns}lastName",  "") or ""
            aka_first = aka.findtext(f"{ns}firstName", "") or ""
            aka_name  = f"{aka_first} {aka_last}".strip() if aka_first else aka_last.strip()
            if aka_name and aka_name != name:
                aliases.append(aka_name)

        country: str | None = None
        for addr in entry.findall(f"{ns}addressList/{ns}address"):
            c = addr.findtext(f"{ns}country", "") or ""
            if c:
                country = c[:10]
                break

        programs = [
            p.text for p in entry.findall(f"{ns}programList/{ns}program") if p.text
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
    offset      = 0
    page_size   = 10    # data.trade.gov は実際には最大10件/ページを返す
    max_offset  = 1000  # API の offset 上限（"Maximum offset is 1000"）
    total_count = 0

    # data.trade.gov は Azure APIM 形式: キーは "subscription-key" ヘッダーで渡す
    headers = {"subscription-key": api_key} if api_key and api_key != "DEMO_KEY" else {}
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        while True:
            if offset > max_offset:
                logger.warning(
                    "CSL %s: offset %d exceeded API max (%d). "
                    "Total %d entries — fetched %d/%d. "
                    "Use additional search filters to retrieve remaining entries.",
                    source_code, offset, max_offset, total_count, len(entries), total_count,
                )
                break

            params = {
                "sources": source_code,
                "size":    page_size,
                "offset":  offset,
            }
            # 429 レートリミット対応: retry-after ヘッダーに従い最大3回リトライ
            for attempt in range(3):
                resp = client.get(CSL_API_URL, params=params, headers=headers)
                if resp.status_code == 429:
                    wait = float(resp.headers.get("retry-after", "2")) + 0.5
                    logger.warning("CSL %s: rate limited (429), waiting %.1fs", source_code, wait)
                    time.sleep(wait)
                    continue
                break
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Trade.gov CSL API returned HTTP {resp.status_code}. "
                    "Register at developer.trade.gov and set TRADE_GOV_API_KEY."
                )
            if not resp.text.strip():
                raise RuntimeError("Trade.gov CSL API returned empty response (rate limit or key issue)")
            data = resp.json()
            total_count = data.get("total", 0)
            time.sleep(0.5)  # ページ間のレートリミット回避

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


def fetch_bis_entity_list_ecfr(timeout: int = 120) -> list[dict[str, Any]]:
    """BIS Entity List を eCFR (15 CFR Part 744 Supplement No. 4) から全件取得する。

    APIキー不要。Trade.gov CSL APIのoffset上限1000制約を回避し全3,420件を取得できる。
    eCFR XML はリアルタイム更新される公式ソース。
    """
    logger.info("Fetching BIS Entity List from eCFR (Part 744 Supplement No. 4)")

    # eCFR は日付ベースのURLを使用。最新版を versions API から取得し、なければ今年の1月1日を使用
    from datetime import date as _date
    ecfr_date = _date.today().replace(month=1, day=1).isoformat()
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as _vc:
            _vr = _vc.get(ECFR_VERSIONS_URL)
            if _vr.status_code == 200:
                _versions = _vr.json().get("content_versions", [])
                if _versions:
                    ecfr_date = sorted(v["date"] for v in _versions)[-1]
    except Exception:
        pass

    url = ECFR_PART744_URL.format(date=ecfr_date)
    logger.info("eCFR URL: %s", url)

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()

    xml_text = resp.text

    # Supplement No. 4 セクションを抽出
    idx_start = xml_text.find('<DIV9 N="Supplement No. 4 to Part 744"')
    if idx_start < 0:
        raise RuntimeError("eCFR Part 744 Supplement No. 4 not found in response")
    idx_end = xml_text.find('<DIV9', idx_start + 1)
    supp4 = xml_text[idx_start:idx_end if idx_end > 0 else len(xml_text)]

    rows = re.findall(r'<TR>(.*?)</TR>', supp4, re.DOTALL)
    if not rows:
        raise RuntimeError("No table rows found in eCFR Supplement No. 4")

    entries: list[dict[str, Any]] = []
    current_country: str = ""

    for row in rows[1:]:  # 先頭ヘッダー行をスキップ
        cells = re.findall(r'<T[DH][^>]*>(.*?)</T[DH]>', row, re.DOTALL)
        texts = [html_module.unescape(re.sub(r'<[^>]+>', ' ', c)).strip() for c in cells]

        if len(texts) < 2:
            continue

        if texts[0].strip():
            current_country = texts[0].strip()[:10]

        entity_raw = texts[1].strip()
        if not entity_raw:
            continue

        # エンティティ名と住所を分離（最初のカンマで分割）
        parts = entity_raw.split(',', 1)
        name    = parts[0].strip()
        address = parts[1].strip() if len(parts) > 1 else ""

        fr_notice = texts[4].strip() if len(texts) > 4 else None

        if not name:
            continue

        entries.append({
            "list_source": "bis_entity",
            "entity_name": name,
            "aliases":     None,
            "address":     address[:500] if address else None,
            "country":     current_country or None,
            "source_id":   f"EL-{name[:80]}-{current_country}" if current_country else f"EL-{name[:80]}",
            "reason":      fr_notice,
            "risk_level":  "high",
            "extra":       {"federal_register_notice": fr_notice, "source": "ecfr"},
        })

    logger.info("eCFR BIS Entity List: parsed %d entries", len(entries))
    return entries


def fetch_bis_entity_list(api_key: str = "DEMO_KEY", timeout: int = 30) -> list[dict[str, Any]]:
    """BIS Entity List を取得する。eCFR（全件・APIキー不要）を優先し、失敗時は CSL API にフォールバック。"""
    try:
        return fetch_bis_entity_list_ecfr(timeout=120)
    except Exception as e:
        logger.warning("eCFR EL fetch failed (%s), falling back to CSL API (offset-limited)", e)
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

def fetch_uk_ofsi(timeout: int = 180) -> list[dict[str, Any]]:
    """UK OFSI 統合制裁リストを CSV から取得・パースする。

    CSV（16MB）を優先使用。失敗時は XML（54MB）にフォールバック。
    """
    try:
        return _fetch_uk_ofsi_csv(timeout=timeout)
    except Exception as e:
        logger.warning("UK OFSI CSV failed (%s), falling back to XML", e)
        return _fetch_uk_ofsi_xml(timeout=timeout)


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


def _fetch_uk_ofsi_csv(timeout: int = 180) -> list[dict[str, Any]]:
    logger.info("Fetching UK OFSI CSV from %s", UK_OFSI_CSV)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(UK_OFSI_CSV)
        resp.raise_for_status()

    text = resp.content.decode("utf-8-sig", errors="replace")

    # CSV先頭にメタデータ行（"Last Updated,..."）があるため実際のヘッダー行まで読み飛ばす
    lines = text.split('\n')
    header_idx = 0
    for i, line in enumerate(lines[:10]):
        if 'Name 6' in line or 'Group ID' in line:
            header_idx = i
            break
    actual_csv = '\n'.join(lines[header_idx:])

    reader = csv.DictReader(io.StringIO(actual_csv))
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
