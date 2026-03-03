"""matrix.json パーサー。

matrix.json の sheets を解析して regulation nodes のリストを返す。
各 node は control_nodes.json の "regulation" タイプのノードに対応する。
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any


def _extract_eccn_refs(cargo_rules: list[dict]) -> list[str]:
    """cargo_rules から ECCN コードを抽出してユニークリストを返す。"""
    eccns: list[str] = []
    for rule in cargo_rules:
        eccn = rule.get("eccn")
        if eccn and isinstance(eccn, str):
            # カンマ区切り / スラッシュ区切りを分割
            for part in re.split(r"[,/、]", eccn):
                part = part.strip()
                if part and re.match(r"^[0-9][A-Z][0-9]", part):
                    eccns.append(part)
    return list(dict.fromkeys(eccns))  # 順序を保ったユニーク


def _extract_parameters(cargo_rules: list[dict]) -> list[dict]:
    """cargo_rules の substances から技術パラメータを抽出する。"""
    params: list[dict] = []
    for rule in cargo_rules:
        meti_ref = rule.get("meti_order_ref", {})
        meti_id  = meti_ref.get("id", "") if isinstance(meti_ref, dict) else ""
        for sub in rule.get("substances", []):
            if isinstance(sub, dict):
                text = sub.get("text") or sub.get("raw", "")
                # 数値・単位パターンを含む項目を技術パラメータとして抽出
                if re.search(r"[\d\.]+\s*(?:GHz|MHz|nm|μm|um|ナノ|マイクロ|kg|W|V|A|cm²|dB|%|Torr|Pa)", text):
                    params.append({
                        "id":       sub.get("id", ""),
                        "text":     text,
                        "meti_ref": meti_id,
                    })
    return params


def _sheet_to_category(sheet_name: str) -> str:
    """シート名からカテゴリキーを生成する。"""
    # 全角数字を半角に変換して先頭数字を取得
    normalized = sheet_name.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    m = re.search(r"(\d+)", normalized)
    return f"EL-{m.group(1)}" if m else sheet_name[:10]


def parse_matrix(matrix_path: pathlib.Path) -> list[dict]:
    """matrix.json を読み込み regulation node リストを返す。

    Returns:
        各要素の形式:
        {
          "id": "EL-7-19",
          "type": "regulation",
          "regime": "fefta",
          "label": "レジスト",
          "item_no": "EL-7-19",
          "meti_ref": "METI-6-19",
          "description": "...",
          "parameters": [...],
          "eccn_explicit": ["1C350", ...],
          "sheet": "７項　エレクトロニクス",
          "sheet_category": "EL-7",
          "_raw": {...},
        }
    """
    with matrix_path.open(encoding="utf-8") as f:
        data = json.load(f)

    sheets: dict[str, Any] = data.get("sheets", {})
    nodes: list[dict] = []

    for sheet_name, sheet_data in sheets.items():
        if not isinstance(sheet_data, dict):
            continue

        export_items: list[dict] = sheet_data.get("export_items", [])
        sheet_category = _sheet_to_category(sheet_name)

        for item in export_items:
            ref = item.get("export_order_ref", {})
            item_id    = ref.get("id", "")
            item_label = item.get("export_order_item", "")

            meti_ref   = item.get("intro_meti_order_ref", {})
            meti_id    = meti_ref.get("id", "") if isinstance(meti_ref, dict) else ""
            description = item.get("intro_meti_order_text", "")

            cargo_rules = item.get("cargo_rules", []) or []
            eccn_refs   = _extract_eccn_refs(cargo_rules)
            parameters  = _extract_parameters(cargo_rules)

            if not item_id:
                continue

            node: dict = {
                "id":            item_id,
                "type":          "regulation",
                "regime":        "fefta",
                "regime_list":   "別表第1",
                "label":         item_label,
                "item_no":       item_id,
                "meti_ref":      meti_id,
                "description":   description,
                "requirement_text": None,   # DB enrich で上書き
                "parameters":    parameters,
                "eccn_explicit": eccn_refs,
                "edges": {
                    "explicit":  [],    # ECCN参照 (後でセット)
                    "derived":   [],    # De Minimis / FDP (将来)
                    "semantic":  [],    # 特許リンク (後でセット)
                },
                "source_refs": {
                    "matrix_json":       item_id,
                    "sheet":             sheet_name,
                    "sheet_category":    sheet_category,
                    "matrix_rule_db_id": None,  # DB enrich で上書き
                },
            }
            nodes.append(node)

    return nodes
