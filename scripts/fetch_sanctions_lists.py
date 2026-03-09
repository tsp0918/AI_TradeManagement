"""制裁リストデータ取得スクリプト。

OFAC SDN XML および BIS Entity List を公式ソースから取得し、
data/source/sanctions/sanctions_cache.json に保存する。

使い方:
    python scripts/fetch_sanctions_lists.py
    python scripts/fetch_sanctions_lists.py --ofac-only
    python scripts/fetch_sanctions_lists.py --bis-api-key YOUR_KEY
    python scripts/fetch_sanctions_lists.py --import  # 取得後、screenigモジュールAPIへ自動インポート
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

# platform-core を import パスに追加
sys.path.insert(0, str(BASE_DIR / "platform-core"))
sys.path.insert(0, str(BASE_DIR / "modules" / "screening"))


def main() -> None:
    parser = argparse.ArgumentParser(description="制裁リストデータ取得")
    parser.add_argument("--ofac-only", action="store_true", help="OFAC SDN のみ取得（BIS をスキップ）")
    parser.add_argument("--bis-api-key", default="DEMO_KEY", help="Trade.gov API キー（デフォルト: DEMO_KEY）")
    parser.add_argument("--import", dest="do_import", action="store_true",
                        help="取得後にスクリーニングモジュール API へ自動インポート")
    args = parser.parse_args()

    from app.services.sanctions_sync import fetch_bis_entity_list, fetch_ofac_sdn

    all_entries: list[dict] = []

    # ── OFAC SDN ────────────────────────────────────────────────────────
    print("[1/2] OFAC SDN XML を取得中… (URL: https://www.treasury.gov/ofac/downloads/sdn.xml)")
    try:
        ofac = fetch_ofac_sdn()
        all_entries.extend(ofac)
        print(f"  → {len(ofac)} 件取得")
    except Exception as e:
        print(f"  → [ERROR] {e}", file=sys.stderr)

    # ── BIS Entity List ─────────────────────────────────────────────────
    if not args.ofac_only:
        print(f"[2/2] BIS Entity List を取得中… (api_key={args.bis_api_key[:8]}...)")
        try:
            bis = fetch_bis_entity_list(api_key=args.bis_api_key)
            all_entries.extend(bis)
            print(f"  → {len(bis)} 件取得")
        except Exception as e:
            print(f"  → [ERROR] {e}", file=sys.stderr)
    else:
        print("[2/2] BIS Entity List スキップ (--ofac-only)")

    if not all_entries:
        print("[ERROR] データ取得失敗。終了します。", file=sys.stderr)
        sys.exit(1)

    # ── キャッシュ保存 ───────────────────────────────────────────────────
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(all_entries, f, ensure_ascii=False, indent=2)
    print(f"\n[SAVED] {CACHE_PATH} ({len(all_entries)} 件)")

    # ── API インポート（オプション）─────────────────────────────────────
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
        print("\n[NEXT] スクリーニングモジュールを起動後、ウォッチリスト画面の")
        print("       「制裁リスト同期 (OFAC / BIS)」ボタンで同期できます。")
        print("       または: python scripts/fetch_sanctions_lists.py --import")


if __name__ == "__main__":
    main()
