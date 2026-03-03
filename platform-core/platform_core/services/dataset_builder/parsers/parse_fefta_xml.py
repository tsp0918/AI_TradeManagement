"""外為法 輸出貿易管理令 XML パーサー。

e-Gov XML フォーマット（Cabinet Order）の 別表第一 を解析し、
各 項（カテゴリ）の公式テキストを抽出する。

出力フォーマット:
  {
    "item_no_kanji": "七",         # 元の漢数字
    "category": 7,                 # 数値 (1-16)
    "short_label": "集積回路等",    # 先頭テキストから生成した短いラベル
    "full_text": "...",            # 貨物列の全テキスト
    "region": "全地域",
    "node_pattern": "EL-7-",      # matrix.json の item_no プレフィックス
  }

使用方法:
    from .parsers.parse_fefta_xml import parse_fefta_xml
    categories = parse_fefta_xml(path)
"""

from __future__ import annotations

import pathlib
import re
import xml.etree.ElementTree as ET
from typing import Any

# ── 漢数字 → アラビア数字 マッピング ────────────────────────────
_KANJI_NUM: dict[str, int] = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9,
    "一〇": 10, "一一": 11, "一二": 12, "一三": 13,
    "一四": 14, "一五": 15, "一六": 16,
    # 複合項: 三の二 → 3 (サブカテゴリとして扱う)
    "三の二": 3,
}

# ── 項番 → 短いラベル ───────────────────────────────────────────
_CATEGORY_LABELS: dict[int, str] = {
    1:  "武器・弾薬",
    2:  "原子力関連",
    3:  "化学兵器関連",
    4:  "ロケット・ミサイル",
    5:  "航空宇宙関連",
    6:  "センサー・レーザー",
    7:  "集積回路・半導体・電子部品",
    8:  "電子計算機",
    9:  "通信装置",
    10: "水中探知・船舶",
    11: "慣性航法装置",
    12: "潜水艦",
    13: "ガスタービンエンジン",
    14: "推進剤・燃料",
    15: "複合材料・繊維",
    16: "その他規制品目（レーザー等）",
}

# ── 項番 → matrix.json item_no プレフィックス ───────────────────
_CATEGORY_NODE_PREFIX: dict[int, str] = {
    1: "EL-1-", 2: "EL-2-", 3: "EL-3-", 4: "EL-4-",
    5: "EL-5-", 6: "EL-6-", 7: "EL-7-", 8: "EL-8-",
    9: "EL-9-", 10: "EL-10-", 11: "EL-11-", 12: "EL-12-",
    13: "EL-13-", 14: "EL-14-", 15: "EL-15-", 16: "EL-16-",
}


def _get_text(elem: ET.Element) -> str:
    """要素ツリー全体の text を再帰的に連結して返す。"""
    return "".join(elem.itertext()).strip()


def _find_all_by_tag(root: ET.Element, local_tag: str) -> list[ET.Element]:
    """名前空間を無視してローカルタグ名で要素を検索する。"""
    results: list[ET.Element] = []
    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag == local_tag:
            results.append(elem)
    return results


def _normalize_item_no(raw: str) -> tuple[str, int]:
    """漢数字の項番 → (正規化文字列, 数値) に変換。"""
    key = raw.strip()
    # "三の二" などの複合項
    if key in _KANJI_NUM:
        return key, _KANJI_NUM[key]
    # 先頭一致フォールバック
    for k, v in sorted(_KANJI_NUM.items(), key=lambda x: -len(x[0])):
        if key.startswith(k):
            return k, v
    return key, 0


def _make_short_label(category: int, full_text: str) -> str:
    """分類番号と全文から短いラベルを生成する。"""
    if category in _CATEGORY_LABELS:
        return _CATEGORY_LABELS[category]
    # フォールバック: 最初の50文字
    first_line = full_text.split("\n")[0][:50].strip()
    return first_line or f"第{category}項"


def parse_fefta_xml(
    xml_path: pathlib.Path | str,
) -> list[dict[str, Any]]:
    """輸出貿易管理令 XML の 別表第一 を解析してカテゴリリストを返す。

    Args:
        xml_path: 輸出貿易管理令.xml のパス

    Returns:
        list of category dicts (item_no_kanji, category, short_label, full_text, ...)
    """
    xml_path = pathlib.Path(xml_path)
    tree = ET.parse(str(xml_path))
    root = tree.getroot()

    # ── 別表第一 を特定 ─────────────────────────────────────────
    appdx_tables = _find_all_by_tag(root, "AppdxTable")
    bekkan1: ET.Element | None = None
    for t in appdx_tables:
        titles = _find_all_by_tag(t, "AppdxTableTitle")
        if titles and "別表第一" in _get_text(titles[0]):
            bekkan1 = t
            break

    if bekkan1 is None:
        return []

    # ── TableRow を処理 ──────────────────────────────────────────
    rows = _find_all_by_tag(bekkan1, "TableRow")
    categories: list[dict[str, Any]] = []

    for row in rows:
        cols = _find_all_by_tag(row, "TableColumn")
        if len(cols) < 2:
            continue

        item_no_raw = _get_text(cols[0])
        goods_text  = _get_text(cols[1])
        region_text = _get_text(cols[2]) if len(cols) > 2 else "全地域"

        # ヘッダー行を除外
        if not item_no_raw or item_no_raw in ("", "貨物", "地域"):
            continue
        # 数値のみの行を除外
        if re.match(r"^[\d\s]+$", item_no_raw):
            continue

        item_no_kanji, category = _normalize_item_no(item_no_raw)
        if category == 0:
            continue

        short_label = _make_short_label(category, goods_text)
        node_pattern = _CATEGORY_NODE_PREFIX.get(category, f"EL-{category}-")

        # 「経済産業省令で定める仕様のもの」など定型句の後を要旨として抽出
        items_text = _extract_item_summary(goods_text)

        categories.append({
            "item_no_kanji": item_no_kanji,
            "category":      category,
            "short_label":   short_label,
            "full_text":     goods_text,
            "items_summary": items_text,
            "region":        region_text,
            "node_pattern":  node_pattern,
        })

    return categories


def _extract_item_summary(goods_text: str) -> str:
    """貨物テキストから（一）（二）... の箇条書き部分を抽出して要約する。"""
    # （数字/文字）パターンで始まる行を抽出
    lines = goods_text.split("\n")
    items: list[str] = []
    for line in lines:
        stripped = line.strip()
        # 括弧付き番号・記号の行
        if re.match(r"^[（(][一二三四五六七八九十\d]+[）)]", stripped):
            # 最初の100文字だけ取る
            items.append(stripped[:100])
    return "；".join(items) if items else goods_text[:200]


# ── カテゴリと規制ノードを照合するユーティリティ ────────────────

def build_category_index(
    categories: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """category 番号 → カテゴリ dict のインデックスを構築する。"""
    return {c["category"]: c for c in categories}


def enrich_nodes_with_fefta_categories(
    nodes: list[dict[str, Any]],
    categories: list[dict[str, Any]],
) -> int:
    """regulation nodes に 別表第一 カテゴリの公式テキストを付与する。

    Returns:
        付与したノード数
    """
    cat_index = build_category_index(categories)
    enriched = 0
    for node in nodes:
        if node.get("type") != "regulation":
            continue
        item_no: str = node.get("item_no", "")
        # EL-7-19 → category=7
        m = re.match(r"EL-(\d+)-", item_no)
        if not m:
            continue
        cat = int(m.group(1))
        if cat not in cat_index:
            continue
        cat_data = cat_index[cat]
        node["fefta_category_text"]    = cat_data["full_text"][:500]
        node["fefta_category_label"]   = cat_data["short_label"]
        node["fefta_category_summary"] = cat_data["items_summary"][:300]
        node["source_refs"]["fefta_xml"] = f"別表第一 {cat_data['item_no_kanji']}の項"
        enriched += 1
    return enriched
