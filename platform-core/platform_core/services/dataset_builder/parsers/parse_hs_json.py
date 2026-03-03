"""HS コード → 外為法 別表第一 近似マッピング。

公式の外為法↔HS 対照表が入手できない場合の実用的な代替実装。
hs2022_6digit.json（5,613件）から輸出管理関連HS品目を抽出し、
キーワード + 章番号ルールで外為法 項との近似マッピングを生成する。

公式対照表（経産省「輸出令別表第一の関係HS番号一覧表」）が入手できた時点で
正式マッピングに置き換える想定。

出力フォーマット:
  {
    "id":           "HS-854231",
    "type":         "hs",
    "hs_code":      "854231",
    "heading":      "8542",
    "chapter":      "85",
    "description":  "Electronic integrated circuits; processors...",
    "fefta_category": 7,              # 推定 外為法 項番号
    "fefta_category_label": "集積回路・半導体・電子部品",
    "match_confidence": "high",       # high / medium / low
    "match_basis":  "heading_rule",   # heading_rule / keyword / chapter_rule
    "edges": {"explicit": [], "derived": [...], "semantic": []},
    "source_refs": {"hs_json": "hs2022_6digit.json"},
  }

使用方法:
    from .parsers.parse_hs_json import parse_hs_json
    hs_nodes = parse_hs_json(path)
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

# ── 外為法 カテゴリ ラベル ────────────────────────────────────────
_FEFTA_LABELS: dict[int, str] = {
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

# ── HS 見出し（4桁）→ 外為法 項 確定マッピング ───────────────────
# 根拠: 経産省 規制品目説明 + WCO HS 2022
# Confidence: high = 対照表または BIS AES 対応で確実
#             medium = 章・説明文から高い確度
#             low  = 章から推測
_HEADING_MAP: dict[str, tuple[int, str]] = {
    # ── 一の項: 武器 ──────────────────────────────────────────
    "9301": (1, "high"),   # Military weapons: artillery
    "9302": (1, "high"),   # Pistols and revolvers
    "9303": (1, "high"),   # Other firearms
    "9304": (1, "high"),   # Other arms
    "9305": (1, "medium"), # Parts of arms
    "9306": (1, "high"),   # Bombs, grenades, ammunition
    "9307": (1, "high"),   # Swords, bayonets
    # ── 二の項: 原子力 ────────────────────────────────────────
    "8401": (2, "high"),   # Nuclear reactors, fuel elements
    "2612": (2, "high"),   # Uranium ores
    "2844": (2, "high"),   # Radioactive elements
    "2845": (2, "high"),   # Stable isotopes
    # ── 三の項: 化学兵器 ──────────────────────────────────────
    # CWC Schedule 1-3 chemicals: 特定 HS 番号（選択的）
    "2921": (3, "medium"), # Amine-function compounds (CWC precursors)
    "2903": (3, "medium"), # Halogenated derivatives
    "2931": (3, "medium"), # Other organo-inorganic compounds
    # ── 四の項: ミサイル ──────────────────────────────────────
    "8807": (4, "high"),   # Parts of aircraft/spacecraft → missiles
    "3601": (4, "high"),   # Propellants; propellants powder
    "3602": (4, "high"),   # Prepared explosives
    "3603": (4, "high"),   # Safety fuses, detonating fuses
    "3604": (4, "medium"), # Fireworks, signalling flares
    # ── 五の項: 航空宇宙 ──────────────────────────────────────
    "8801": (5, "medium"), # Balloons and gliders
    "8802": (5, "high"),   # Aircraft; aeroplanes, helicopters
    "8803": (5, "medium"), # Parts of aircraft
    "8805": (5, "medium"), # Aircraft launch gear
    # ── 六の項: センサー・レーザー ────────────────────────────
    "9013": (6, "high"),   # Liquid crystal devices, lasers, optical apparatus
    "9027": (6, "medium"), # Instruments for analysis
    "9031": (6, "medium"), # Measuring/checking instruments
    "9001": (6, "medium"), # Optical fibres
    "9002": (6, "medium"), # Optical lenses
    # ── 七の項: 集積回路・半導体 ──────────────────────────────
    "8541": (7, "high"),   # Semiconductor devices, diodes, transistors
    "8542": (7, "high"),   # Electronic integrated circuits
    "8543": (7, "medium"), # Electrical machines: particle accelerators
    "8486": (7, "high"),   # Semiconductor manufacturing machines
    # ── 八の項: 電子計算機 ────────────────────────────────────
    "8471": (8, "medium"), # Computers and data processing
    "8473": (8, "medium"), # Parts of computers
    # ── 九の項: 通信 ──────────────────────────────────────────
    "8517": (9, "medium"), # Telephone, router, network equipment
    "8525": (9, "medium"), # Transmission apparatus for radio-broadcasting
    "8526": (9, "medium"), # Radar, radio navigational aid
    # ── 一〇の項: 水中探知 ────────────────────────────────────
    "9014": (10, "medium"), # Navigational instruments (compasses, sextants)
    # ── 一一の項: 慣性航法 ────────────────────────────────────
    "9015": (11, "medium"), # Surveying, hydrographic instruments
    "9012": (11, "medium"), # Microscopes
    # ── 一二の項: 潜水艦 ──────────────────────────────────────
    "8901": (12, "medium"), # Cruise ships
    "8906": (12, "high"),   # Military vessels
    # ── 一三の項: ガスタービン ────────────────────────────────
    "8411": (13, "high"),   # Turbojets, turbopropellers
    # ── 一四の項: 推進剤 ──────────────────────────────────────
    "3605": (14, "medium"), # Matches
    "2804": (14, "medium"), # Hydrogen, rare gases
    # ── 一五の項: 複合材料 ────────────────────────────────────
    "3926": (15, "medium"), # Articles of plastics
    "5601": (15, "medium"), # Wadding and felt
    "5603": (15, "medium"), # Nonwovens
    "7019": (15, "high"),   # Glass fibres and articles thereof (CFRP related)
    "6813": (15, "medium"), # Friction material (asbestos-free)
    # ── 一六の項: レーザー等 ──────────────────────────────────
    "9010": (16, "medium"), # Apparatus for photographic labs, negatoscopes
}

# ── キーワード → 外為法 項 補完ルール ────────────────────────────
# heading_map にない品目をキーワードで補完する
_KEYWORD_RULES: list[tuple[list[str], int, str]] = [
    # (keywords, fefta_category, confidence)
    (["integrated circuit", "microprocessor", "microcontroller",
      "semiconductor", "wafer", "epitaxial"], 7, "medium"),
    (["photoresist", "lithograph", "photomask", "reticle",
      "immersion", "extreme ultraviolet", "EUV"], 7, "medium"),
    (["laser diode", "laser device", "laser beam", "optical amplifier"], 6, "medium"),
    (["fibre optic", "fiber optic", "optical fibre"], 6, "medium"),
    (["nuclear", "radioactive", "uranium", "enriched", "fissile"], 2, "medium"),
    (["missile", "rocket propulsion", "solid propellant"], 4, "medium"),
    (["aircraft engine", "jet engine", "turbofan", "turbojet"], 13, "medium"),
    (["composite fibre", "carbon fibre", "kevlar", "aramid"], 15, "medium"),
    (["cryptograph", "encryption", "cipher"], 9, "medium"),
    (["inertial navigation", "gyroscope", "accelerometer"], 11, "medium"),
    (["sonar", "hydrophone", "underwater acoustic"], 10, "medium"),
]

# ── 優先的に抽出する HS 章（その他は除外） ───────────────────────
_PRIORITY_CHAPTERS: set[str] = {
    "22", "26", "28", "29", "30", "35", "36", "38",
    "72", "73", "74", "76", "84", "85", "86", "87",
    "88", "89", "90", "91", "93",
}


def _match_by_heading(hs_code: str) -> tuple[int, str, str] | None:
    """4桁見出しで外為法 項を確定マッチする。
    Returns: (fefta_category, confidence, match_basis) or None
    """
    heading = hs_code[:4]
    if heading in _HEADING_MAP:
        cat, conf = _HEADING_MAP[heading]
        return cat, conf, "heading_rule"
    return None


def _match_by_keyword(description: str) -> tuple[int, str, str] | None:
    """説明文キーワードで外為法 項を推定する。"""
    desc_lower = description.lower()
    for keywords, cat, conf in _KEYWORD_RULES:
        if any(kw.lower() in desc_lower for kw in keywords):
            return cat, conf, "keyword"
    return None


def parse_hs_json(
    hs_json_path: pathlib.Path | str,
    include_low_confidence: bool = False,
) -> list[dict[str, Any]]:
    """hs2022_6digit.json を読み込み、輸出管理関連 HS ノードを生成する。

    Args:
        hs_json_path: hs2022_6digit.json のパス
        include_low_confidence: low マッチを含めるか（デフォルト False）

    Returns:
        HS ノードのリスト（各ノードに derived_edge あり）
    """
    hs_json_path = pathlib.Path(hs_json_path)
    with hs_json_path.open(encoding="utf-8") as f:
        all_items: list[dict] = json.load(f)

    nodes: list[dict[str, Any]] = []

    for item in all_items:
        hs_code    = str(item.get("hs_code", "")).strip()
        chapter    = str(item.get("chapter", "")).strip().zfill(2)
        heading    = hs_code[:4] if len(hs_code) >= 4 else ""
        description = item.get("description_en", "") or ""

        # 優先章フィルター
        if chapter not in _PRIORITY_CHAPTERS:
            continue

        # マッチング
        result = _match_by_heading(hs_code)
        if result is None:
            result = _match_by_keyword(description)
        if result is None:
            continue

        fefta_cat, confidence, match_basis = result

        if confidence == "low" and not include_low_confidence:
            continue

        # Derived edge: HS → regulation category (EL-{n}-*)
        node_pattern = f"EL-{fefta_cat}-"
        derived_edge: dict[str, Any] = {
            "target_type":    "regulation_category",
            "target_pattern": node_pattern,
            "fefta_category": fefta_cat,
            "relation":       "hs_fefta_approx",
            "confidence":     confidence,
            "match_basis":    match_basis,
            "label":          f"外為法 {_FEFTA_LABELS.get(fefta_cat, str(fefta_cat))}の項（近似）",
        }

        nodes.append({
            "id":                   f"HS-{hs_code}",
            "type":                 "hs",
            "hs_code":              hs_code,
            "heading":              heading,
            "chapter":              chapter,
            "chapter_description":  item.get("chapter_description", ""),
            "heading_description":  item.get("heading_description", ""),
            "description":          description,
            "section":              item.get("section", ""),
            "fefta_category":       fefta_cat,
            "fefta_category_label": _FEFTA_LABELS.get(fefta_cat, ""),
            "match_confidence":     confidence,
            "match_basis":          match_basis,
            "edges": {
                "explicit": [],
                "derived":  [derived_edge],
                "semantic": [],
            },
            "source_refs": {
                "hs_json": hs_json_path.name,
            },
        })

    return nodes


def build_hs_lookup_index(
    nodes: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """hs_code → HS ノードの逆引きインデックスを構築する。"""
    return {n["hs_code"]: n for n in nodes}
