"""
fetch_jp_naccs.py
─────────────────────────────────────────────────────────────────────────────
税関 NACCS コードリストから JP 9桁統計品目番号を取得し、
data/staging/jp_hs_local.json へ保存するスクリプト。

データソース:
  https://www.customs.go.jp/tariff/2025_04_01/naccscode202504_X.html
  (X = 1〜10, 全10ファイル)

出力フォーマット:
  {
    "meta": {"source": "...", "fetched_at": "...", "count": N},
    "items": [
      {
        "hs9":  "850440000",   # 9桁（区切り文字なし）
        "hs6":  "850440",      # 先頭6桁
        "code": "8504.40-000", # 元の表示形式
        "desc": "...",         # 英語品名
        "unit": "KG"
      }, ...
    ]
  }

使い方:
  python scripts/fetch_jp_naccs.py
  python scripts/fetch_jp_naccs.py --year 2025 --out data/staging/jp_hs_local.json
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import html as html_mod

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── 設定 ──────────────────────────────────────────────────────────────────────

DEFAULT_YEAR = 2025
NUM_FILES    = 10
TIMEOUT      = 30.0
DELAY        = 1.0   # サーバー負荷軽減のためリクエスト間隔

_CODE_RE = re.compile(r"\d{4}\.\d{2}-\d{3}")
_TD_RE   = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)
_TR_RE   = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
_TAG_RE  = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    return html_mod.unescape(_TAG_RE.sub("", html)).replace("\u3000", "").strip()


def parse_items(html: str) -> list[dict]:
    """HTML テキストから 9桁コード + 品名を抽出する。"""
    items: list[dict] = []
    for tr_m in _TR_RE.finditer(html):
        row_html = tr_m.group(1)
        tds = [_strip_tags(td) for td in _TD_RE.findall(row_html)]
        if not tds or not _CODE_RE.match(tds[0]):
            continue
        code = tds[0]
        desc = tds[3] if len(tds) > 3 else ""
        unit = tds[5] if len(tds) > 5 else (tds[4] if len(tds) > 4 else "")
        hs9  = code.replace(".", "").replace("-", "")
        items.append({
            "hs9":  hs9,
            "hs6":  hs9[:6],
            "code": code,
            "desc": desc,
            "unit": unit,
        })
    return items


def fetch_section(url: str) -> str:
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content.decode("shift_jis", errors="replace")


def build(year: int = DEFAULT_YEAR, out_path: Path = None) -> None:
    date_str = f"{year}_04_01"
    yymm4    = f"{year}04"          # 例: 202504

    all_items: list[dict] = []
    seen: set[str] = set()

    for n in range(1, NUM_FILES + 1):
        url = (
            f"https://www.customs.go.jp/tariff/{date_str}"
            f"/naccscode{yymm4}_{n}.html"
        )
        logger.info("Fetching (%d/%d) %s ...", n, NUM_FILES, url)
        try:
            html = fetch_section(url)
        except httpx.HTTPStatusError as e:
            logger.warning("HTTP %d — %s (skip)", e.response.status_code, url)
            if e.response.status_code == 404:
                break
            continue
        except Exception as e:
            logger.warning("Error: %s — skip", e)
            continue

        items = parse_items(html)
        logger.info("  → %d codes parsed", len(items))

        for item in items:
            if item["hs9"] not in seen:
                seen.add(item["hs9"])
                all_items.append(item)

        if n < NUM_FILES:
            time.sleep(DELAY)

    logger.info("Total unique codes: %d", len(all_items))

    if not all_items:
        logger.error("No items parsed. Check URL/HTML structure.")
        sys.exit(1)

    payload = {
        "meta": {
            "source":     f"Japan Customs NACCS code list {date_str}",
            "source_url": f"https://www.customs.go.jp/tariff/{date_str}/",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "count":      len(all_items),
        },
        "items": all_items,
    }

    if out_path is None:
        out_path = (
            Path(__file__).resolve().parents[1]
            / "data" / "staging" / "jp_hs_local.json"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Saved → %s  (%d items)", out_path, len(all_items))


def main():
    parser = argparse.ArgumentParser(description="JP NACCS コードリスト取得")
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR,
                        help=f"年度 (default: {DEFAULT_YEAR})")
    parser.add_argument("--out",  type=Path, default=None,
                        help="出力 JSON パス (default: data/staging/jp_hs_local.json)")
    args = parser.parse_args()
    build(year=args.year, out_path=args.out)


if __name__ == "__main__":
    main()
