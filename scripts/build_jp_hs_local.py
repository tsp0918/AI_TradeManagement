"""
build_jp_hs_local.py
────────────────────────────────────────────────────────────────────────────
日本 統計品目番号（9桁）ルックアップテーブル構築スクリプト。

Phase A  [実装済み] : hs_all_merged.json の 6 桁コードに "000" を補完し
                      ベースラインとして jp_hs_local.json を生成。
Phase B  [将来対応] : 財務省「実行関税率表」Excel/CSV を取り込み、
                      6桁→実際の 9桁細分を展開して上書きマージ。

JP 9桁コード形式:
  国際 HS 6桁 + 日本独自 3桁 = 統計品目番号 (9 桁)
  例: 850440 → 8504400000  （6桁+000、実務では末尾に "0" を付けて 10桁表記もある）
  本システムは 9桁 ("XXXXXXXXX") を標準とする。

出力 JSON 構造:
  {
    "meta": { "built_at": "...", "total": N, "source": "..." },
    "records": [
      {
        "hs6":         "850440",
        "jp_code":     "850440000",   ← 9桁
        "description": "...",
        "tariff_rate": null,          ← Phase B で埋める
        "tariff_type": null,          ← MFN / FTA / GSP
        "unit":        null,          ← Phase B で埋める
        "source":      "seed_000pad"  ← Phase B: "mof_tariff_2024"
      }, ...
    ]
  }

使い方:
  cd /Users/takehirosato/Desktop/AI_TradeManagement
  python scripts/build_jp_hs_local.py                   # Phase A ベースライン生成
  python scripts/build_jp_hs_local.py --mof path/to/tariff.csv  # Phase B マージ（将来）
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_ROOT      = Path(__file__).resolve().parents[1]
_HS_JSON   = _ROOT / "data" / "staging" / "hs_all_merged.json"
_OUT_JSON  = _ROOT / "data" / "staging" / "jp_hs_local.json"


# ── Phase A: 6桁→9桁 000パディング ──────────────────────────────────────────

def build_seed(hs_json: Path) -> list[dict]:
    """hs_all_merged.json の 6桁レコードを読み込み 9桁ベースラインを生成。"""
    logger.info("Loading %s", hs_json)
    with open(hs_json, encoding="utf-8") as f:
        raw = json.load(f)
    records_raw = raw.get("records", raw) if isinstance(raw, dict) else raw

    seed: list[dict] = []
    for r in records_raw:
        code = r.get("hs_code", "")
        if len(code) != 6:
            continue
        seed.append({
            "hs6":         code,
            "jp_code":     code + "000",   # 9桁
            "description": r.get("description", ""),
            "tariff_rate": None,
            "tariff_type": None,
            "unit":        None,
            "source":      "seed_000pad",
        })

    logger.info("Seed records: %d", len(seed))
    return seed


# ── Phase B: 財務省 CSVマージ（将来実装） ─────────────────────────────────────

def merge_mof_csv(seed: list[dict], csv_path: Path) -> list[dict]:
    """財務省「実行関税率表」CSVを取り込み seed を置き換えマージ。

    CSVカラム想定（財務省フォーマット - 将来的に調整が必要）:
      統計品目番号, 品名, 基本税率, 暫定税率, WTO税率, 単位
    """
    import csv

    logger.info("Loading MoF CSV: %s", csv_path)

    # hs6 → seedレコードのインデックスを構築
    idx: dict[str, list[int]] = {}
    for i, r in enumerate(seed):
        idx.setdefault(r["hs6"], []).append(i)

    mof_records: list[dict] = []
    skipped = 0
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # カラム名は実際のCSVに合わせて調整
            jp_code_raw = row.get("統計品目番号", "").strip().replace(".", "").replace("-", "")
            jp_code = jp_code_raw[:9] if len(jp_code_raw) >= 9 else jp_code_raw.ljust(9, "0")
            hs6 = jp_code[:6] if len(jp_code) >= 6 else ""
            if not hs6 or len(jp_code) < 9:
                skipped += 1
                continue

            tariff_str = row.get("WTO税率", row.get("基本税率", "")).strip().rstrip("%")
            tariff_rate: float | None = None
            try:
                tariff_rate = float(tariff_str) / 100.0
            except (ValueError, TypeError):
                pass

            mof_records.append({
                "hs6":         hs6,
                "jp_code":     jp_code,
                "description": row.get("品名", ""),
                "tariff_rate": tariff_rate,
                "tariff_type": "MFN" if tariff_rate is not None else None,
                "unit":        row.get("単位", None) or None,
                "source":      "mof_tariff",
            })

    logger.info("MoF records parsed: %d  (skipped %d)", len(mof_records), skipped)

    # hs6 でグループ化して seed を上書き
    mof_by_hs6: dict[str, list[dict]] = {}
    for r in mof_records:
        mof_by_hs6.setdefault(r["hs6"], []).append(r)

    # seed からMoFで上書きされる hs6 を削除し、MoF分を追加
    replaced = set(mof_by_hs6.keys())
    merged = [r for r in seed if r["hs6"] not in replaced]
    for recs in mof_by_hs6.values():
        merged.extend(recs)

    logger.info("Merged: seed=%d  mof=%d  total=%d  replaced_hs6=%d",
                len(seed), len(mof_records), len(merged), len(replaced))
    return merged


# ── 出力 ─────────────────────────────────────────────────────────────────────

def write_output(records: list[dict], source_label: str) -> None:
    _OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "meta": {
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "total":    len(records),
            "source":   source_label,
        },
        "records": records,
    }
    _OUT_JSON.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    size_kb = _OUT_JSON.stat().st_size / 1024
    logger.info("Saved %s  (%.0f KB, %d records)", _OUT_JSON, size_kb, len(records))


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Build JP local HS lookup table")
    parser.add_argument("--mof", metavar="CSV_PATH", default=None,
                        help="財務省 実行関税率表 CSV を指定（Phase B マージ）")
    parser.add_argument("--dry-run", action="store_true",
                        help="出力せずにサンプルを表示して終了")
    args = parser.parse_args()

    if not _HS_JSON.exists():
        logger.error("hs_all_merged.json not found: %s", _HS_JSON)
        sys.exit(1)

    records = build_seed(_HS_JSON)

    if args.mof:
        mof_path = Path(args.mof)
        if not mof_path.exists():
            logger.error("MoF CSV not found: %s", mof_path)
            sys.exit(1)
        records = merge_mof_csv(records, mof_path)
        source_label = f"mof_tariff+seed  csv={mof_path.name}"
    else:
        source_label = "seed_000pad  base=hs_all_merged.json"

    if args.dry_run:
        logger.info("[dry-run] sample output:")
        for r in records[:5]:
            logger.info("  %s", r)
        return

    write_output(records, source_label)


if __name__ == "__main__":
    main()
