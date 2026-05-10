"""
build_ontology.py
──────────────────────────────────────────────────────────────────────────────
輸出管理コンプライアンス向けオントロジーを構築するスクリプト。

生成物:
  data/ontology/eccn_hierarchy.json    ECCN 階層構造（カテゴリ/製品グループ/統制理由/CR）
  data/ontology/fefta_eccn_xref.json   外為法別表↔ECCN 対比表
  data/ontology/ipc_eccn_bidir.json    IPC-ECCN 双方向マッピング
  data/ontology/hs_eccn_scored.json    HS-ECCN マッピング（Confidence 付き）
  data/ontology/ontology_graph.json    全関係を統合したグラフ（platform-core API 用）

使い方:
  python scripts/build_ontology.py
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_ROOT    = Path(__file__).resolve().parents[1]
_STAGING = _ROOT / "data" / "staging"
_OUT     = _ROOT / "data" / "ontology"
_OUT.mkdir(parents=True, exist_ok=True)

# ──────────────────────────────────────────────────────────────────────────────
# 定数
# ──────────────────────────────────────────────────────────────────────────────

ECCN_CATEGORIES = {
    "0": {"label_en": "Nuclear & Radiological", "label_ja": "核・放射線"},
    "1": {"label_en": "Chemical, Biological, Toxins & Munitions", "label_ja": "化学・生物・毒素・火工品"},
    "2": {"label_en": "Materials Processing", "label_ja": "材料加工"},
    "3": {"label_en": "Electronics", "label_ja": "電子機器"},
    "4": {"label_en": "Computers", "label_ja": "計算機"},
    "5": {"label_en": "Telecommunications & Information Security", "label_ja": "通信・情報セキュリティ"},
    "6": {"label_en": "Sensors & Lasers", "label_ja": "センサー・レーザー"},
    "7": {"label_en": "Navigation & Avionics", "label_ja": "航法・航空電子"},
    "8": {"label_en": "Marine", "label_ja": "海洋"},
    "9": {"label_en": "Aerospace & Propulsion", "label_ja": "航空宇宙・推進"},
}

PRODUCT_GROUPS = {
    "A": {"label_en": "Systems, Equipment & Components", "label_ja": "システム・機器・部品"},
    "B": {"label_en": "Test, Inspection & Production Equipment", "label_ja": "試験・検査・製造設備"},
    "C": {"label_en": "Materials", "label_ja": "材料・化学品"},
    "D": {"label_en": "Software", "label_ja": "ソフトウェア"},
    "E": {"label_en": "Technology", "label_ja": "技術"},
}

REASON_FOR_CONTROL_LABELS = {
    "AT":  {"label_en": "Anti-Terrorism",              "label_ja": "テロ対策 (AT)"},
    "CB":  {"label_en": "Chemical & Biological Wpns",  "label_ja": "化学・生物兵器 (CB)"},
    "CC":  {"label_en": "Crime Control",               "label_ja": "犯罪取締 (CC)"},
    "CW":  {"label_en": "Chemical Weapons",            "label_ja": "化学兵器 (CW)"},
    "EI":  {"label_en": "Encryption Items",            "label_ja": "暗号品目 (EI)"},
    "FC":  {"label_en": "Firearms Conv.",              "label_ja": "銃器 (FC)"},
    "MT":  {"label_en": "Missile Technology",          "label_ja": "ミサイル技術 (MT)"},
    "NP":  {"label_en": "Nuclear Non-Proliferation",   "label_ja": "核不拡散 (NP)"},
    "NS":  {"label_en": "National Security",           "label_ja": "国家安全保障 (NS)"},
    "RS":  {"label_en": "Regional Stability",          "label_ja": "地域安定 (RS)"},
    "SL":  {"label_en": "Surreptitious Listening",     "label_ja": "盗聴対策 (SL)"},
    "SS":  {"label_en": "Short Supply",                "label_ja": "供給制限 (SS)"},
    "UN":  {"label_en": "United Nations",              "label_ja": "国連制裁 (UN)"},
    "XP":  {"label_en": "Computers",                   "label_ja": "計算機 (XP)"},
}

# 外為法 輸出令別表第1 ↔ EAR/ECCN 対比表（CISTEC 対比表に基づく公知情報）
FEFTA_ECCN_XREF = [
    {
        "fefta_item_no": "1",
        "fefta_label": "武器・軍用品",
        "fefta_description": "武器、弾薬、軍用機・軍用艦船・軍用車両等",
        "eccn_patterns": [],
        "usml_categories": ["I","II","III","IV","V","VI","VII","VIII","IX","X","XI","XII","XIII","XIV","XV","XVI","XVII","XVIII","XIX","XX","XXI"],
        "wassenaar_ml_categories": ["ML1","ML2","ML3","ML4","ML5","ML6","ML7","ML8","ML9","ML10","ML11","ML12","ML13","ML14","ML15","ML16","ML17","ML18","ML19","ML20","ML21","ML22"],
        "ear99_possible": False,
        "catch_all_applicable": False,
        "notes": "主にUSML/ITAR管理品目。EAR '600系列'も含む",
    },
    {
        "fefta_item_no": "2",
        "fefta_label": "核弾頭・大量破壊兵器搭載用ミサイル等",
        "fefta_description": "核弾頭、化学・生物兵器、大量破壊兵器搭載用飛しょう体",
        "eccn_patterns": ["0A002","0E001","9A004"],
        "eccn_prefixes": ["0A","0E","9A"],
        "usml_categories": ["I","IV"],
        "wassenaar_ml_categories": [],
        "ear99_possible": False,
        "catch_all_applicable": False,
        "notes": "MTCR対象品目を含む",
    },
    {
        "fefta_item_no": "3",
        "fefta_label": "電子応用機器等",
        "fefta_description": "電子部品・集積回路・真空管・マイクロ波素子等",
        "eccn_patterns": ["3A001","3A002","3B001","3C001","3D001","3E001"],
        "eccn_prefixes": ["3A","3B","3C","3D","3E"],
        "usml_categories": ["XI","XV"],
        "wassenaar_ml_categories": [],
        "ear99_possible": False,
        "catch_all_applicable": True,
        "primary_eccns": ["3A001","3A002","3B001","3C001"],
        "notes": "半導体・集積回路・電子部品が中心",
    },
    {
        "fefta_item_no": "4",
        "fefta_label": "計算機",
        "fefta_description": "コンピュータ、デジタル計算機、AI処理機器等",
        "eccn_patterns": ["4A003","4D001","4E001"],
        "eccn_prefixes": ["4A","4D","4E"],
        "usml_categories": ["XI"],
        "wassenaar_ml_categories": [],
        "ear99_possible": False,
        "catch_all_applicable": True,
        "primary_eccns": ["4A003","4D001"],
        "notes": "高性能計算機・クラスタが対象",
    },
    {
        "fefta_item_no": "5",
        "fefta_label": "通信機器等（暗号を除く）",
        "fefta_description": "通信機器、無線機器、信号処理機器等",
        "eccn_patterns": ["5A001","5B001","5D001","5E001"],
        "eccn_prefixes": ["5A001","5B001","5D001","5E001"],
        "usml_categories": ["XI","XIII"],
        "wassenaar_ml_categories": [],
        "ear99_possible": False,
        "catch_all_applicable": True,
        "primary_eccns": ["5A001","5E001"],
        "notes": "",
    },
    {
        "fefta_item_no": "5の2",
        "fefta_label": "情報セキュリティ（暗号）",
        "fefta_description": "暗号機器・暗号ソフトウェア・暗号技術",
        "eccn_patterns": ["5A002","5D002","5E002"],
        "eccn_prefixes": ["5A002","5D002","5E002"],
        "usml_categories": [],
        "wassenaar_ml_categories": [],
        "ear99_possible": False,
        "catch_all_applicable": True,
        "primary_eccns": ["5A002","5D002"],
        "notes": "EI (Encryption Items) 管理",
    },
    {
        "fefta_item_no": "6",
        "fefta_label": "センサー・レーザー",
        "fefta_description": "センサー、光学機器、赤外線検知器、レーザー等",
        "eccn_patterns": ["6A001","6A002","6B001","6C001","6D001","6E001"],
        "eccn_prefixes": ["6A","6B","6C","6D","6E"],
        "usml_categories": ["XII","XIV"],
        "wassenaar_ml_categories": [],
        "ear99_possible": False,
        "catch_all_applicable": True,
        "primary_eccns": ["6A001","6A002","6E001"],
        "notes": "ソナー・レーダー・IRカメラ等",
    },
    {
        "fefta_item_no": "7",
        "fefta_label": "航法計器等",
        "fefta_description": "慣性航法装置、ジャイロスコープ、GPS機器等",
        "eccn_patterns": ["7A001","7A002","7B001","7D001","7E001"],
        "eccn_prefixes": ["7A","7B","7D","7E"],
        "usml_categories": ["XII","XV"],
        "wassenaar_ml_categories": [],
        "ear99_possible": False,
        "catch_all_applicable": True,
        "primary_eccns": ["7A001","7A002"],
        "notes": "INS・IMU・加速度計・ジャイロ",
    },
    {
        "fefta_item_no": "8",
        "fefta_label": "航空機等",
        "fefta_description": "航空機、航空機エンジン、アビオニクス等",
        "eccn_patterns": ["9A001","9A610","9B001","9D001","9E001"],
        "eccn_prefixes": ["9A001","9A610","9B","9D","9E"],
        "usml_categories": ["VIII","IX"],
        "wassenaar_ml_categories": ["ML10"],
        "ear99_possible": False,
        "catch_all_applicable": True,
        "primary_eccns": ["9A001","9A610","9E001"],
        "notes": "EAR '600系列' 9x610も含む",
    },
    {
        "fefta_item_no": "9",
        "fefta_label": "推進装置等",
        "fefta_description": "ジェットエンジン、ロケットエンジン、宇宙機推進系等",
        "eccn_patterns": ["9A001","9A004","9A115","9B001"],
        "eccn_prefixes": ["9A001","9A004","9A115"],
        "usml_categories": ["IV","XV","XX"],
        "wassenaar_ml_categories": [],
        "ear99_possible": False,
        "catch_all_applicable": True,
        "primary_eccns": ["9A001","9A004"],
        "notes": "ロケット・宇宙打ち上げシステム等（MTCR管理品目と重複）",
    },
    {
        "fefta_item_no": "10",
        "fefta_label": "工作機械等",
        "fefta_description": "数値制御工作機械、精密加工機械等",
        "eccn_patterns": ["2B001","2B004","2B201","2D001","2E001"],
        "eccn_prefixes": ["2B","2D001","2E001"],
        "usml_categories": [],
        "wassenaar_ml_categories": [],
        "ear99_possible": False,
        "catch_all_applicable": True,
        "primary_eccns": ["2B001","2B004"],
        "notes": "精度・速度が規制基準",
    },
    {
        "fefta_item_no": "11",
        "fefta_label": "先進複合材料等",
        "fefta_description": "炭素繊維、セラミックス複合材、金属マトリクス複合材等",
        "eccn_patterns": ["1C010","1C011","1C012","1C210","1D001","1E001"],
        "eccn_prefixes": ["1C01","1C21","1D","1E"],
        "usml_categories": [],
        "wassenaar_ml_categories": [],
        "ear99_possible": False,
        "catch_all_applicable": True,
        "primary_eccns": ["1C010","1C011"],
        "notes": "炭素繊維（CFRP等）が主要対象",
    },
    {
        "fefta_item_no": "12",
        "fefta_label": "有機薬品等（化学兵器前駆体）",
        "fefta_description": "化学兵器前駆物質、殺虫剤原体等（CWC Schedule物質）",
        "eccn_patterns": ["1C350","1C351","1C355","1C395","1D001","1E001"],
        "eccn_prefixes": ["1C35","1C39"],
        "usml_categories": [],
        "wassenaar_ml_categories": [],
        "ear99_possible": False,
        "catch_all_applicable": True,
        "primary_eccns": ["1C350","1C355"],
        "notes": "化学兵器条約（CWC）スケジュール物質",
    },
    {
        "fefta_item_no": "13",
        "fefta_label": "生物剤等",
        "fefta_description": "病原微生物、毒素、生物兵器製造設備等",
        "eccn_patterns": ["1C351","1C353","1C354","1B001","1E001"],
        "eccn_prefixes": ["1C351","1C352","1C353","1B001"],
        "usml_categories": [],
        "wassenaar_ml_categories": [],
        "ear99_possible": False,
        "catch_all_applicable": True,
        "primary_eccns": ["1C351","1C353"],
        "notes": "BWC（生物兵器禁止条約）関連物質",
    },
    {
        "fefta_item_no": "14",
        "fefta_label": "原子炉等",
        "fefta_description": "原子炉、核燃料処理設備、ウラン濃縮設備等",
        "eccn_patterns": ["0A001","0B001","0C001","0D001","0E001"],
        "eccn_prefixes": ["0A","0B","0C","0D","0E"],
        "usml_categories": [],
        "wassenaar_ml_categories": [],
        "ear99_possible": False,
        "catch_all_applicable": False,
        "primary_eccns": ["0A001","0B001","0C001"],
        "notes": "NSG（原子力供給国グループ）管理品目",
    },
    {
        "fefta_item_no": "15",
        "fefta_label": "火薬・爆発物等",
        "fefta_description": "爆発物、推進剤、火工品等",
        "eccn_patterns": ["1C111","1C018","1C240","1D001"],
        "eccn_prefixes": ["1C111","1C018","1C240"],
        "usml_categories": ["IV","V"],
        "wassenaar_ml_categories": ["ML8"],
        "ear99_possible": False,
        "catch_all_applicable": True,
        "primary_eccns": ["1C111","1C018"],
        "notes": "固体燃料ロケット推進剤等",
    },
    {
        "fefta_item_no": "16",
        "fefta_label": "その他（省令別表付属書）",
        "fefta_description": "貨物等省令別表付属書に規定するその他の品目",
        "eccn_patterns": [],
        "eccn_prefixes": [],
        "usml_categories": [],
        "wassenaar_ml_categories": [],
        "ear99_possible": True,
        "catch_all_applicable": True,
        "primary_eccns": [],
        "notes": "EAR99 品目を含む補完的カテゴリ",
    },
]

# キャッチオール規制対象
CATCH_ALL_TYPES = {
    "wmd": {"label_ja": "大量破壊兵器キャッチオール", "fefta_items": ["1","2","3","4","5","5の2","6","7","8","9","14","15"]},
    "conventional": {"label_ja": "通常兵器キャッチオール", "fefta_items": ["1","3","4","5","6","7","8","9","10"]},
}

# ──────────────────────────────────────────────────────────────────────────────
# ① ECCN 階層 JSON 生成
# ──────────────────────────────────────────────────────────────────────────────

def build_eccn_hierarchy() -> dict:
    logger.info("ECCN 階層 JSON 生成中...")
    ccl_path = _STAGING / "ccl_eccn_entries_v8.json"
    if not ccl_path.exists():
        logger.error("ccl_eccn_entries_v8.json not found")
        return {}

    raw = json.loads(ccl_path.read_text(encoding="utf-8"))
    entries = raw.get("entries", [])

    # カテゴリ別・製品グループ別ツリー
    tree: dict = {}
    eccn_index: dict = {}

    for e in entries:
        eccn = e.get("eccn", "").strip()
        if not eccn or len(eccn) < 4:
            continue
        cat = eccn[0]
        pg  = eccn[1] if len(eccn) > 1 else "?"

        cat_node = tree.setdefault(cat, {
            **ECCN_CATEGORIES.get(cat, {"label_en": cat, "label_ja": cat}),
            "eccns": {},
        })
        pg_key = f"{cat}{pg}"
        pg_node = cat_node["eccns"].setdefault(pg_key, {
            **PRODUCT_GROUPS.get(pg, {"label_en": pg, "label_ja": pg}),
            "product_group": pg,
            "eccns": [],
        })

        # ECCN-FEFTA 対比（prefixで逆引き）
        fefta_items = _eccn_to_fefta_items(eccn)
        # 技術パラメータ参照フラグ
        has_params = bool(e.get("technical_notes", "").strip())

        node = {
            "eccn": eccn,
            "label_en": _short_heading(e.get("heading", "")),
            "category": cat,
            "product_group": pg,
            "reason_for_control": e.get("reason_for_control", []),
            "reason_labels": [REASON_FOR_CONTROL_LABELS.get(r, {"label_ja": r}) for r in e.get("reason_for_control", [])],
            "related_controls": _clean_text(e.get("related_controls", "")),
            "fefta_item_nos": fefta_items,
            "has_technical_notes": has_params,
            "items_controlled_snippet": _clean_text(e.get("items_controlled", ""))[:200],
        }
        pg_node["eccns"].append(node)
        eccn_index[eccn] = node

    result = {
        "_version": "1.0",
        "_built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "_source": "ccl_eccn_entries_v8.json",
        "total_eccns": len(eccn_index),
        "categories": tree,
        "eccn_index": eccn_index,
    }
    _OUT.joinpath("eccn_hierarchy.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("ECCN 階層: %d エントリ → eccn_hierarchy.json", len(eccn_index))
    return eccn_index


def _eccn_to_fefta_items(eccn: str) -> list[str]:
    matched = []
    for row in FEFTA_ECCN_XREF:
        patterns  = row.get("eccn_patterns", [])
        prefixes  = row.get("eccn_prefixes", [])
        if eccn in patterns:
            matched.append(row["fefta_item_no"])
        elif any(eccn.startswith(p) for p in prefixes if p):
            matched.append(row["fefta_item_no"])
    return sorted(set(matched))


def _short_heading(text: str) -> str:
    if not text:
        return ""
    text = text.strip()
    # "3A001 Electronic items..." → "Electronic items..."
    text = re.sub(r"^\w\w\d{3,4}\s+", "", text)
    return text[:120]


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# ──────────────────────────────────────────────────────────────────────────────
# ② 外為法↔ECCN 対比表 JSON 生成
# ──────────────────────────────────────────────────────────────────────────────

def build_fefta_eccn_xref(eccn_index: dict) -> dict:
    logger.info("外為法↔ECCN 対比表生成中...")

    # ECCN → 外為法 逆引きを確定した ECCN データで補完
    eccn_to_fefta: dict[str, list[str]] = defaultdict(list)
    for row in FEFTA_ECCN_XREF:
        item_no = row["fefta_item_no"]
        # exact matches
        for e in row.get("eccn_patterns", []):
            eccn_to_fefta[e].append(item_no)
        # prefix matches against known ECCNs
        for pfx in row.get("eccn_prefixes", []):
            for eccn in eccn_index:
                if eccn.startswith(pfx) and item_no not in eccn_to_fefta[eccn]:
                    eccn_to_fefta[eccn].append(item_no)

    # FEFTA item → ECCN リスト（整理済み）
    fefta_to_eccns: dict[str, list[str]] = defaultdict(list)
    for eccn, items in eccn_to_fefta.items():
        for item in items:
            if eccn not in fefta_to_eccns[item]:
                fefta_to_eccns[item].append(eccn)

    xref_rows = []
    for row in FEFTA_ECCN_XREF:
        item_no = row["fefta_item_no"]
        eccns = sorted(fefta_to_eccns.get(item_no, []))
        xref_rows.append({
            **row,
            "eccns_resolved": eccns,
            "eccn_count": len(eccns),
        })

    result = {
        "_version": "1.0",
        "_built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "_note": "外為法 輸出令別表第1 ↔ EAR/ECCN 対比表（CISTEC 対比表準拠）",
        "total_fefta_items": len(xref_rows),
        "eccn_to_fefta_index": {k: sorted(set(v)) for k, v in eccn_to_fefta.items()},
        "fefta_items": xref_rows,
    }
    _OUT.joinpath("fefta_eccn_xref.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    coverage = sum(1 for r in xref_rows if r["eccn_count"] > 0)
    logger.info("外為法 対比表: %d項 → ECCN 解決済み %d項", len(xref_rows), coverage)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# ③ IPC-ECCN 双方向マッピング
# ──────────────────────────────────────────────────────────────────────────────

def build_ipc_eccn_bidir() -> dict:
    logger.info("IPC-ECCN 双方向マッピング生成中...")
    ipc_map_path = _STAGING / "ipc_eccn_mapping.json"
    if not ipc_map_path.exists():
        logger.error("ipc_eccn_mapping.json not found")
        return {}

    raw = json.loads(ipc_map_path.read_text(encoding="utf-8"))
    mappings = raw.get("mappings", [])

    # 正方向: IPC prefix → ECCN list
    ipc_to_eccn: dict[str, dict] = {}
    # 逆方向: ECCN → IPC prefix list
    eccn_to_ipc: dict[str, list[dict]] = defaultdict(list)

    for m in mappings:
        eccn = m.get("eccn", "").strip()
        hints = m.get("ipc_hints", [])
        conf  = m.get("confidence", "medium")
        rationale = m.get("rationale", "")
        if not eccn or not hints:
            continue

        for ipc in hints:
            ipc = ipc.strip()
            if ipc not in ipc_to_eccn:
                ipc_to_eccn[ipc] = {"ipc_prefix": ipc, "eccns": []}
            ipc_to_eccn[ipc]["eccns"].append({
                "eccn": eccn,
                "confidence": conf,
                "rationale": rationale,
            })
            eccn_to_ipc[eccn].append({
                "ipc_prefix": ipc,
                "confidence": conf,
                "rationale": rationale,
            })

    result = {
        "_version": "1.0",
        "_built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "_note": "IPC-ECCN 双方向マッピング。forward: IPC→ECCN, reverse: ECCN→IPC",
        "total_ipc_prefixes": len(ipc_to_eccn),
        "total_eccns": len(eccn_to_ipc),
        "forward": ipc_to_eccn,
        "reverse": {k: sorted(v, key=lambda x: x["confidence"], reverse=True)
                    for k, v in eccn_to_ipc.items()},
    }
    _OUT.joinpath("ipc_eccn_bidir.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("IPC-ECCN 双方向: %d IPC prefix / %d ECCN", len(ipc_to_eccn), len(eccn_to_ipc))
    return result


# ──────────────────────────────────────────────────────────────────────────────
# ④ HS-ECCN Confidence score 付与
# ──────────────────────────────────────────────────────────────────────────────

CONFIDENCE_REASONS = {
    "exact":      {"score": 0.95, "label": "完全一致（公式リスト）"},
    "high":       {"score": 0.85, "label": "高信頼（主要マッピング）"},
    "medium":     {"score": 0.65, "label": "中信頼（分類推定）"},
    "low":        {"score": 0.40, "label": "低信頼（技術類似性による推定）"},
    "inferred":   {"score": 0.50, "label": "推定（IPC経由の間接マッピング）"},
}

def build_hs_eccn_scored() -> dict:
    logger.info("HS-ECCN Confidence score 付与中...")
    hs_path = _STAGING / "hs_eccn_mapping.json"
    if not hs_path.exists():
        logger.error("hs_eccn_mapping.json not found")
        return {}

    raw = json.loads(hs_path.read_text(encoding="utf-8"))
    # records は {hs_code: {eccn, confidence, method, ...}} の dict 形式
    records_raw = raw.get("records", {})
    if isinstance(records_raw, list):
        records_iter = ((str(r.get("hs_code", r.get("hs",""))), r) for r in records_raw)
    else:
        records_iter = records_raw.items()

    scored = []
    confidence_dist: dict[str, int] = defaultdict(int)

    for hs, rec in records_iter:
        hs   = str(hs).strip()
        eccn = str(rec.get("eccn", "")).strip()
        if not hs or not eccn:
            continue

        # 既存の信頼度フィールド（float or string）を正規化
        raw_conf = rec.get("confidence", 0.0)
        method   = str(rec.get("method", "")).lower()

        if isinstance(raw_conf, float):
            if raw_conf >= 0.9:
                conf_key = "exact"
            elif raw_conf >= 0.75:
                conf_key = "high"
            elif raw_conf >= 0.5:
                conf_key = "medium"
            else:
                conf_key = "low"
        elif str(raw_conf).lower() in CONFIDENCE_REASONS:
            conf_key = str(raw_conf).lower()
        elif "ipc" in method or "infer" in method:
            conf_key = "inferred"
        elif "manual" in method or "official" in method:
            conf_key = "high"
        elif len(hs) >= 8:
            conf_key = "high"
        elif len(hs) >= 6:
            conf_key = "medium"
        else:
            conf_key = "low"

        conf_info = CONFIDENCE_REASONS[conf_key]
        confidence_dist[conf_key] += 1
        eccns = rec.get("eccns", [eccn])

        scored.append({
            "hs_code": hs,
            "eccn": eccn,
            "eccns": eccns,
            "method": method,
            "fefta_item": rec.get("fefta_item"),
            "confidence": conf_key,
            "confidence_score": conf_info["score"],
            "confidence_label": conf_info["label"],
        })

    result = {
        "_version": "1.0",
        "_built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total": len(scored),
        "confidence_distribution": dict(confidence_dist),
        "records": scored,
    }
    _OUT.joinpath("hs_eccn_scored.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("HS-ECCN scored: %d件 distribution=%s", len(scored), dict(confidence_dist))
    return result


# ──────────────────────────────────────────────────────────────────────────────
# ⑤ 統合オントロジーグラフ JSON 生成
# ──────────────────────────────────────────────────────────────────────────────

def build_ontology_graph(eccn_index: dict, ipc_bidir: dict, hs_scored: dict, fefta_xref: dict) -> None:
    logger.info("統合オントロジーグラフ生成中...")

    # Layer B から ECCN タグ付きパテント数集計
    layer_b_path = _STAGING / "layer_b_meta.json"
    patent_eccn_counts: dict[str, int] = defaultdict(int)
    if layer_b_path.exists():
        b = json.loads(layer_b_path.read_text())
        for rec in b.get("records", []):
            for e in rec.get("eccn_tags", []):
                patent_eccn_counts[e] += 1

    # Layer D から ECCN 論文数集計
    layer_d_path = _STAGING / "layer_d_meta.json"
    paper_eccn_counts: dict[str, int] = defaultdict(int)
    if layer_d_path.exists():
        d = json.loads(layer_d_path.read_text())
        for rec in d.get("records", []):
            for e in rec.get("eccn_tags", []):
                paper_eccn_counts[e] += 1

    # FEFTA → ECCN 逆引き
    fefta_to_eccns: dict[str, list[str]] = {
        r["fefta_item_no"]: r["eccns_resolved"]
        for r in fefta_xref.get("fefta_items", [])
    }
    eccn_to_fefta_idx: dict[str, list[str]] = {
        k: v for k, v in fefta_xref.get("eccn_to_fefta_index", {}).items()
    }

    # HS → ECCN index
    hs_to_eccns: dict[str, list[dict]] = defaultdict(list)
    for rec in hs_scored.get("records", []):
        hs_to_eccns[rec["hs_code"]].append({
            "eccn": rec["eccn"],
            "confidence": rec["confidence"],
            "confidence_score": rec["confidence_score"],
        })

    # ECCN → HS 逆引き
    eccn_to_hs: dict[str, list[dict]] = defaultdict(list)
    for hs, items in hs_to_eccns.items():
        for item in items:
            eccn_to_hs[item["eccn"]].append({
                "hs_code": hs,
                "confidence": item["confidence"],
                "confidence_score": item["confidence_score"],
            })

    # IPC → ECCN / ECCN → IPC
    ipc_forward: dict = ipc_bidir.get("forward", {})
    ipc_reverse: dict = ipc_bidir.get("reverse", {})

    # ノード生成
    nodes = {}
    for eccn, info in eccn_index.items():
        nodes[eccn] = {
            "id": eccn,
            "type": "ECCN",
            "label_en": info.get("label_en", ""),
            "category": info.get("category", ""),
            "product_group": info.get("product_group", ""),
            "category_label_ja": ECCN_CATEGORIES.get(info.get("category",""), {}).get("label_ja",""),
            "product_group_label_ja": PRODUCT_GROUPS.get(info.get("product_group",""), {}).get("label_ja",""),
            "reason_for_control": info.get("reason_for_control", []),
            "fefta_item_nos": info.get("fefta_item_nos", []),
            "has_technical_notes": info.get("has_technical_notes", False),
            # エビデンス数（Layer B/D）
            "patent_count": patent_eccn_counts.get(eccn, 0),
            "paper_count": paper_eccn_counts.get(eccn, 0),
            # 関係リスト
            "relations": {
                "fefta_items": eccn_to_fefta_idx.get(eccn, []),
                "ipc_prefixes": [x["ipc_prefix"] for x in ipc_reverse.get(eccn, [])],
                "hs_codes_count": len(eccn_to_hs.get(eccn, [])),
            },
        }

    result = {
        "_version": "1.0",
        "_built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "_description": "輸出管理コンプライアンス オントロジーグラフ（platform-core API 用）",
        "stats": {
            "eccn_nodes": len(nodes),
            "ipc_mappings": len(ipc_forward),
            "hs_eccn_mappings": len(hs_scored.get("records", [])),
            "fefta_items": len(fefta_xref.get("fefta_items", [])),
            "eccns_with_patents": sum(1 for e in nodes if nodes[e]["patent_count"] > 0),
            "eccns_with_papers": sum(1 for e in nodes if nodes[e]["paper_count"] > 0),
            "eccns_with_fefta": sum(1 for e in nodes if nodes[e]["relations"]["fefta_items"]),
            "eccns_with_ipc": sum(1 for e in nodes if nodes[e]["relations"]["ipc_prefixes"]),
            "eccns_with_hs": sum(1 for e in nodes if nodes[e]["relations"]["hs_codes_count"] > 0),
        },
        # Lookup indexes for API queries
        "eccn_nodes": nodes,
        "ipc_to_eccn": ipc_forward,
        "eccn_to_ipc": ipc_reverse,
        "hs_to_eccn": dict(hs_to_eccns),
        "eccn_to_hs": {k: sorted(v, key=lambda x: -x["confidence_score"])[:10]
                       for k, v in eccn_to_hs.items()},
        "fefta_to_eccn": fefta_to_eccns,
        "eccn_to_fefta": eccn_to_fefta_idx,
        "fefta_items": FEFTA_ECCN_XREF,
        "eccn_categories": ECCN_CATEGORIES,
        "product_groups": PRODUCT_GROUPS,
        "reason_for_control_labels": REASON_FOR_CONTROL_LABELS,
    }
    _OUT.joinpath("ontology_graph.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    s = result["stats"]
    logger.info(
        "オントロジーグラフ: ECCN=%d, IPC=%d, HS=%d, FEFTA=%d, 特許付き=%d, 論文付き=%d",
        s["eccn_nodes"], s["ipc_mappings"], s["hs_eccn_mappings"], s["fefta_items"],
        s["eccns_with_patents"], s["eccns_with_papers"],
    )


# ──────────────────────────────────────────────────────────────────────────────
# メイン
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("=== オントロジー構築開始 ===")

    eccn_index = build_eccn_hierarchy()
    fefta_xref = build_fefta_eccn_xref(eccn_index)
    ipc_bidir  = build_ipc_eccn_bidir()
    hs_scored  = build_hs_eccn_scored()
    build_ontology_graph(eccn_index, ipc_bidir, hs_scored, fefta_xref)

    logger.info("=== 完了 → data/ontology/ に 5 ファイル生成 ===")
    for f in sorted(_OUT.glob("*.json")):
        size = f.stat().st_size // 1024
        logger.info("  %s (%dKB)", f.name, size)


if __name__ == "__main__":
    main()
