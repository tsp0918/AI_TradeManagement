"""制裁リストデータ取得スクリプト（全7ソース対応）。

OFAC SDN / BIS Entity,UVL,MEU,DPL / EU統合制裁 / UK OFSI を公式ソースから取得し、
data/source/sanctions/sanctions_cache.json に保存する。

使い方:
    python scripts/fetch_sanctions_lists.py
    python scripts/fetch_sanctions_lists.py --sources ofac_sdn,eu_consolidated
    python scripts/fetch_sanctions_lists.py --csl-api-key YOUR_KEY
    python scripts/fetch_sanctions_lists.py --import   # 取得後、screenigモジュールAPIへ自動インポート

利用可能なソース:
    ofac_sdn, bis_entity, bis_uvl, bis_meu, bis_dpl, eu_consolidated, uk_ofsi
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE_DIR      = Path(__file__).resolve().parents[1]
CACHE_DIR     = BASE_DIR / "data" / "source" / "sanctions"
CACHE_PATH    = CACHE_DIR / "sanctions_cache.json"
SCREENING_URL = "http://localhost:8005"

sys.path.insert(0, str(BASE_DIR / "platform-core"))
sys.path.insert(0, str(BASE_DIR / "modules" / "screening"))

ALL_SOURCE_KEYS = ["ofac_sdn", "bis_entity", "bis_uvl", "bis_meu", "bis_dpl", "eu_consolidated", "uk_ofsi"]


def main() -> None:
    parser = argparse.ArgumentParser(description="制裁リストデータ取得（全7ソース）")
    parser.add_argument(
        "--sources",
        default="all",
        help="取得するソース（カンマ区切り）。デフォルト: all\n"
             f"選択肢: {', '.join(ALL_SOURCE_KEYS)}",
    )
    parser.add_argument("--csl-api-key", default="DEMO_KEY", help="Trade.gov API キー")
    parser.add_argument(
        "--import", dest="do_import", action="store_true",
        help="取得後にスクリーニングモジュール API へ自動インポート",
    )
    args = parser.parse_args()

    from app.services.sanctions_sync import (
        fetch_ofac_sdn,
        fetch_bis_entity_list,
        fetch_bis_unverified,
        fetch_bis_meu,
        fetch_bis_dpl,
        fetch_eu_consolidated,
        fetch_uk_ofsi,
    )

    FETCH_MAP = {
        "ofac_sdn":        (fetch_ofac_sdn,        {}),
        "bis_entity":      (fetch_bis_entity_list,  {"api_key": args.csl_api_key}),
        "bis_uvl":         (fetch_bis_unverified,   {"api_key": args.csl_api_key}),
        "bis_meu":         (fetch_bis_meu,          {"api_key": args.csl_api_key}),
        "bis_dpl":         (fetch_bis_dpl,          {"api_key": args.csl_api_key}),
        "eu_consolidated": (fetch_eu_consolidated,  {}),
        "uk_ofsi":         (fetch_uk_ofsi,          {}),
    }

    requested: list[str]
    if args.sources.strip().lower() == "all":
        requested = ALL_SOURCE_KEYS
    else:
        requested = [s.strip() for s in args.sources.split(",") if s.strip() in FETCH_MAP]
        unknown   = [s.strip() for s in args.sources.split(",") if s.strip() not in FETCH_MAP]
        if unknown:
            print(f"[WARNING] 不明なソース: {unknown}", file=sys.stderr)

    all_entries: list[dict] = []
    for i, src_key in enumerate(requested, 1):
        fn, kwargs = FETCH_MAP[src_key]
        print(f"[{i}/{len(requested)}] {src_key} を取得中…")
        try:
            entries = fn(**kwargs)
            all_entries.extend(entries)
            print(f"  → {len(entries)} 件取得")
        except Exception as e:
            print(f"  → [ERROR] {e}", file=sys.stderr)

    if not all_entries:
        print("[ERROR] データ取得失敗。終了します。", file=sys.stderr)
        sys.exit(1)

    # キャッシュ保存
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(all_entries, f, ensure_ascii=False, indent=2)
    print(f"\n[SAVED] {CACHE_PATH} ({len(all_entries)} 件)")

    # ソース別集計
    from collections import Counter
    counts = Counter(e["list_source"] for e in all_entries)
    print("\n  ソース別件数:")
    for src, cnt in sorted(counts.items()):
        print(f"    {src:20s}: {cnt:,d} 件")

    # API インポート（オプション）
    if args.do_import:
        import httpx
        print(f"\n[IMPORT] {SCREENING_URL}/api/watchlist/import へ送信中…")
        BATCH = 500
        total_imported = 0
        for start in range(0, len(all_entries), BATCH):
            batch = all_entries[start:start + BATCH]
            try:
                resp = httpx.post(
                    f"{SCREENING_URL}/api/watchlist/import",
                    json=batch,
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
                total_imported += data.get("imported", len(batch))
                print(f"  バッチ [{start}:{start+len(batch)}] → {data}")
            except Exception as e:
                print(f"  [ERROR] バッチ [{start}:] 失敗: {e}", file=sys.stderr)

        print(f"[DONE] インポート完了: {total_imported} 件")
    else:
        print("\n[NEXT] スクリーニングモジュールを起動後、以下のいずれかで同期できます:")
        print("  ・ウォッチリスト画面の「全制裁リスト同期」ボタン")
        print("  ・python scripts/fetch_sanctions_lists.py --import")


if __name__ == "__main__":
    main()
