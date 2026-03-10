"""
collect/egov_fefta.py
e-Gov 法令 API から「貨物等省令」（輸出貿易管理令別表第一及び外国為替令別表の
規定に基づき貨物又は技術を定める省令）を取得・パースする。

API: https://elaws.e-gov.go.jp/api/1/
  - 法令検索: GET /lawsearch?lawName={name}
  - 条文取得: GET /lawdata/{law_id}

取得対象:
  「貨物等省令」= 輸出貿易管理令別表第一... 省令（METI告示の技術基準パラメータ）
  この省令が外為法の各ノード（EL-xx-x 相当）に対応する技術要件を規定している。

出力: List[FeftaArticle]
  {
    "item_id":        str,   # EL-1-1 等、FEFTA別表の項番に対応
    "article_no":     str,   # 省令条番号
    "title":          str,   # 項目見出し
    "requirement_text": str, # 技術要件（そのまま or 要約）
    "parameters":     list,  # 数値パラメータ抽出（例: "融点 2000°C以上"）
    "raw_text":       str,   # 原文
  }
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any
from xml.etree import ElementTree as ET

import requests

logger = logging.getLogger(__name__)

EGOV_API_BASE    = "https://elaws.e-gov.go.jp/api/1"
# 貨物等省令の正式名称（部分一致で検索）
FEFTA_LAW_NAME   = "貨物等省令"
# 輸出貿易管理令の法令番号（参照用）
FEFTA_ORDER_NAME = "輸出貿易管理令"

FeftaArticle = dict[str, Any]


def fetch_fefta_ministerial_ordinance(
    timeout: int = 30,
    cache_path: str | None = None,
) -> list[FeftaArticle]:
    """
    e-Gov API から貨物等省令を取得・パースして FeftaArticle リストを返す。

    DRY_RUN / キャッシュ利用を想定:
      cache_path が存在すればネットワーク取得をスキップしてキャッシュを返す。
    """
    import json
    from pathlib import Path

    if cache_path and Path(cache_path).exists():
        logger.info("Using cached file: %s", cache_path)
        data = json.loads(Path(cache_path).read_text(encoding="utf-8"))
        return data

    # 1. 法令ID 検索
    law_id = _search_law_id(FEFTA_LAW_NAME, timeout)
    if not law_id:
        logger.warning(
            "貨物等省令が e-Gov で見つかりませんでした。"
            "法令名を確認して law_id を手動指定してください。"
        )
        return []

    logger.info("Found law_id: %s", law_id)

    # 2. 条文XML 取得
    xml_text = _fetch_law_xml(law_id, timeout)
    if not xml_text:
        return []

    # 3. パース
    articles = _parse_fefta_xml(xml_text)
    logger.info("Parsed %d articles from 貨物等省令", len(articles))

    if cache_path:
        p = Path(cache_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Cached to %s", p)

    return articles


def _search_law_id(law_name: str, timeout: int) -> str | None:
    """e-Gov 法令検索 API で法令IDを取得。"""
    url = f"{EGOV_API_BASE}/lawsearch"
    params = {"lawName": law_name}
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        # 最初にヒットした法令IDを返す
        for law_el in root.iter("LawId"):
            if law_el.text:
                return law_el.text.strip()
    except Exception as exc:
        logger.error("e-Gov 法令検索失敗: %s", exc)
    return None


def _fetch_law_xml(law_id: str, timeout: int) -> str | None:
    """e-Gov 条文取得 API で条文XMLを取得。"""
    url = f"{EGOV_API_BASE}/lawdata/{law_id}"
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        logger.error("e-Gov 条文取得失敗 (law_id=%s): %s", law_id, exc)
        return None


def _parse_fefta_xml(xml_text: str) -> list[FeftaArticle]:
    """
    e-Gov 法令 XML から条文を抽出して FeftaArticle リストを構築する。

    e-Gov XML 構造:
      <Law> → <LawBody> → <MainProvision>
        → <Chapter> → <Article> → <Paragraph> → <ParagraphSentence>
                                 → <Item> → <ItemSentence>
    """
    articles: list[FeftaArticle] = []

    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError as exc:
        logger.error("XML parse error: %s", exc)
        return articles

    # MainProvision 以下の Article / Item を再帰探索
    for article_el in root.iter("Article"):
        article_no_el = article_el.find("ArticleTitle")
        article_no    = (article_no_el.text or "").strip() if article_no_el is not None else ""

        for item_el in article_el.iter("Item"):
            item_title_el = item_el.find("ItemTitle")
            item_title    = (item_title_el.text or "").strip() if item_title_el is not None else ""

            # 条文テキスト収集
            raw_parts: list[str] = []
            for sentence_el in item_el.iter("Sentence"):
                if sentence_el.text:
                    raw_parts.append(sentence_el.text.strip())
            raw_text = "\n".join(raw_parts)

            if not raw_text:
                continue

            # EL 項番の推定（"第1号" "別表第1の1" 等から heuristic で ID 生成）
            item_id = _infer_item_id(article_no, item_title)

            # 数値パラメータ抽出
            params = _extract_parameters(raw_text)

            articles.append(
                {
                    "item_id":           item_id,
                    "article_no":        article_no,
                    "title":             item_title,
                    "requirement_text":  raw_text[:500] if len(raw_text) > 500 else raw_text,
                    "parameters":        params,
                    "raw_text":          raw_text,
                }
            )

    return articles


# ── ヘルパー ──────────────────────────────────────────────────────────────────

_ITEM_ID_RE  = re.compile(r"第(\d+)号")
_ARTICLE_RE  = re.compile(r"第(\d+)条")
_PARAM_RE    = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*"
    r"(°C|℃|K|MHz|GHz|THz|nm|μm|mm|cm|m|km|"
    r"kg|g|t|W|kW|MW|V|kV|A|Hz|Bq|Gy|Sv|MPa|GPa|%|ppm|ppb|ビット|bit|バイト|byte)",
    re.IGNORECASE,
)


def _infer_item_id(article_no: str, item_title: str) -> str:
    """
    条文番号・項番から EL-x-x 形式の推定IDを生成。
    完全な対応は builder.py 側の explicit マッピングで補完する。
    """
    art_m  = _ARTICLE_RE.search(article_no)
    item_m = _ITEM_ID_RE.search(item_title)

    art_num  = art_m.group(1)  if art_m  else "0"
    item_num = item_m.group(1) if item_m else "0"

    return f"EL-{art_num}-{item_num}"


def _extract_parameters(text: str) -> list[dict]:
    """テキストから数値パラメータを抽出して構造化する。"""
    params = []
    for m in _PARAM_RE.finditer(text):
        params.append({"value": m.group(1), "unit": m.group(2)})
    return params
