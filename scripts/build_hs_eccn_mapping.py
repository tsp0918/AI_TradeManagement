"""
build_hs_eccn_mapping.py
─────────────────────────────────────────────────────────────────────────────
HS コード → ECCN 包括マッピングを生成する。

データソース（優先順）:
  1. 手動精密マッピング: _HS_HEADING_TO_ECCN（88件 heading / 8件 chapter）
  2. FEFTA チェーン: hs_fefta_mapping_v2.json → FEFTA item_no
                      → _FEFTA_ITEM_TO_ECCN（外為法↔EAR 対照）
  3. フォールバック: chapter（2桁）→ ECCN

出力: data/staging/hs_eccn_mapping.json
  {
    "version": "2",
    "built_at": "...",
    "total": N,
    "records": {
      "850440": {
        "eccn":       "3A001",
        "eccns":      ["3A001"],          # 複数候補
        "confidence": 0.85,               # 0.0〜1.0
        "method":     "heading_manual",   # manual|fefta_chain|chapter_fallback
        "fefta_item": "7"                 # FEFTA 経由の場合
      }, ...
    }
  }

使い方:
  python scripts/build_hs_eccn_mapping.py
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_STAGING = Path(__file__).resolve().parents[1] / "data" / "staging"

# ── 外為法（輸出令別表第一）item_no → ECCN 対照表 ─────────────────────────────
# 出典: CISTEC 外為法↔EAR 対照表・BIS CCL 概要に基づく実務対応表
# 各品目番号に複数 ECCN が対応する場合は代表的なものをリスト
_FEFTA_ITEM_TO_ECCN: dict[str, list[str]] = {
    "1":  ["USML"],            # 武器・弾薬 → USML（EAR対象外）
    "2":  ["1C350", "1C351", "2B350"],  # 化学兵器・生物兵器原料
    "3":  ["9A001", "9A115", "1C111"],  # ミサイル・ロケット
    "4":  ["0C001", "0A001", "0B001"],  # 原子力（核）関連
    "5":  ["1C010", "1C015", "1C016"],  # 先端素材（複合材・高温超電導等）
    "6":  ["2B001", "2B004", "3A002"],  # 工作機械・軸受・精密機器
    "7":  ["3A001", "3C001", "3A002"],  # 電子部品・集積回路
    "8":  ["4A003", "4D001"],           # 電子計算機
    "9":  ["5A001", "5A002", "5E002"],  # センサー・通信・暗号
    "10": ["6A001", "6A002", "7A001"],  # センサー・加速度計・航行
    "11": ["7A001", "7A002", "7E001"],  # 航行・誘導装置
    "12": ["8A001", "8B001"],           # 海洋関連
    "13": ["9A610", "9A004", "1C111"],  # 航空宇宙用エンジン等
    "14": ["1C010", "1C012", "1C017"],  # 先端材料
    "15": ["1C010", "1C007"],           # 繊維複合材料
}

# ── 手動 heading (4桁) → ECCN マッピング（精密版）─────────────────────────────
# integrations.py の _HS_HEADING_TO_ECCN と同内容（confidence=1.0）
_HS_HEADING_MANUAL: dict[str, str] = {
    # Cat 0
    "2612": "0C001", "2844": "0C001", "2845": "0C006", "8401": "0B001",
    # Cat 1
    "2818": "1C010", "2826": "1C011", "2850": "1C350", "3802": "1C117",
    "3818": "3C001", "6815": "1C010", "6903": "1C010", "7228": "1C117",
    "7508": "1C117", "8104": "1C010", "8108": "1C010", "8109": "1C010",
    # Cat 2
    "8418": "2B350", "8419": "2B350", "8421": "2B352", "8456": "2B001",
    "8457": "2B001", "8458": "2B001", "8459": "2B001", "8460": "2B001",
    "8461": "2B001", "8462": "2B001", "8464": "2B001", "8466": "2B001",
    "8479": "2B350", "8481": "2A226", "8484": "2B350",
    # Cat 3
    "8486": "3B001", "8504": "3A001", "8532": "3A001", "8533": "3A001",
    "8534": "3A001", "8536": "3A001", "8541": "3A001", "8542": "3A001",
    "8543": "3A001",
    # Cat 4
    "8471": "4A003", "8473": "4A003",
    # Cat 5
    "8517": "5A002", "8518": "5A002", "8525": "5A001", "8526": "6A008",
    "8527": "5A001", "8528": "5A001", "8529": "6A008",
    # Cat 6
    "8544": "6A002", "9001": "6A002", "9002": "6A002", "9003": "6A002",
    "9005": "6A003", "9006": "6A003", "9007": "6A003", "9010": "6A003",
    "9011": "6A001", "9012": "6A001", "9013": "6A005", "9014": "7A001",
    "9015": "7A002", "9025": "7A002", "9026": "7A002", "9027": "6A001",
    "9030": "3A002", "9031": "7A002",
    # Cat 7 / 9
    "8803": "9A610", "8801": "9A610", "8802": "9A610", "8804": "1C111",
    "8807": "9A610", "8411": "9A610", "8412": "1C111", "8414": "2B350",
    # Chemicals
    "2811": "1C350", "2812": "1C350", "2828": "1C350",
    "2903": "1C350", "2904": "1C350", "2910": "1C350", "2920": "1C350",
    "2921": "1C350", "2931": "1C350", "3002": "1C351", "3824": "1C350",
}

# HS chapter (2桁) → ECCN フォールバック
_HS_CHAPTER_FALLBACK: dict[str, str] = {
    "28": "1C350", "29": "1C350", "30": "1C351", "38": "1C117",
    "84": "2B001", "85": "3A001", "90": "6A002", "93": "USML",
}


def build() -> None:
    # hs_fefta_mapping_v2.json をロード
    fefta_path = _STAGING / "hs_fefta_mapping_v2.json"
    if not fefta_path.exists():
        logger.warning("hs_fefta_mapping_v2.json not found — FEFTA chain skipped")
        fefta_rev: dict = {}
    else:
        fefta_data = json.loads(fefta_path.read_text(encoding="utf-8"))
        fefta_rev = fefta_data.get("reverse_index", {})
    logger.info("Loaded hs_fefta reverse_index: %d entries", len(fefta_rev))

    records: dict[str, dict] = {}

    # 1. 手動 heading マッピング（最高信頼度）
    for heading, eccn in _HS_HEADING_MANUAL.items():
        # heading から対応する HS6 を fefta_rev から収集
        matched = [hs for hs in fefta_rev if hs.startswith(heading)]
        if not matched:
            # fefta_rev になくても heading 単位で記録
            records[f"{heading}00"] = {
                "eccn": eccn, "eccns": [eccn], "confidence": 1.0,
                "method": "heading_manual", "fefta_item": None,
            }
        else:
            for hs6 in matched:
                records[hs6] = {
                    "eccn": eccn, "eccns": [eccn], "confidence": 1.0,
                    "method": "heading_manual", "fefta_item": None,
                }

    # 2. FEFTA チェーン（hs_fefta_mapping_v2 → FEFTA item → ECCN）
    for hs6, fefta_items in fefta_rev.items():
        if hs6 in records:
            continue  # 手動マッピングを優先

        best = max(fefta_items, key=lambda x: x.get("confidence", 0))
        item_no = str(best.get("fefta_item_no", ""))
        fefta_conf = float(best.get("confidence", 0.5))
        eccn_list = _FEFTA_ITEM_TO_ECCN.get(item_no, [])

        if not eccn_list:
            continue

        # USML はスキップ（EAR 対象外）
        eccn_list_ear = [e for e in eccn_list if e != "USML"]
        if not eccn_list_ear:
            records[hs6] = {
                "eccn": "USML", "eccns": ["USML"], "confidence": fefta_conf * 0.85,
                "method": "fefta_chain", "fefta_item": item_no,
            }
            continue

        primary = eccn_list_ear[0]
        records[hs6] = {
            "eccn": primary,
            "eccns": eccn_list_ear,
            "confidence": round(fefta_conf * 0.85, 2),
            "method": "fefta_chain",
            "fefta_item": item_no,
        }

    # 3. Chapter フォールバック（まだ未カバーの HS6 に対して）
    all_hs6_in_fefta = set(fefta_rev.keys())
    for hs6 in all_hs6_in_fefta:
        if hs6 in records:
            continue
        chapter = hs6[:2]
        eccn = _HS_CHAPTER_FALLBACK.get(chapter)
        if eccn:
            records[hs6] = {
                "eccn": eccn, "eccns": [eccn], "confidence": 0.4,
                "method": "chapter_fallback", "fefta_item": None,
            }

    # 統計
    total = len(records)
    by_method = {}
    for r in records.values():
        m = r["method"]
        by_method[m] = by_method.get(m, 0) + 1
    logger.info("Total records: %d", total)
    for m, c in sorted(by_method.items()):
        logger.info("  %s: %d", m, c)

    # 保存
    out_path = _STAGING / "hs_eccn_mapping.json"
    payload = {
        "version":  "2",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "total":    total,
        "method_counts": by_method,
        "records":  records,
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Saved → %s", out_path)


if __name__ == "__main__":
    build()
