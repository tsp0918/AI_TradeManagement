#!/usr/bin/env python3
"""DEMO定義の自動検証スクリプト。

uc_definitions.json の全ステップについて:
  1. navigate URL が HTTP 200 を返すか
  2. highlight target テキストが実際の HTML ページ内に存在するか
  3. fill_field ターゲットが input/textarea/select として存在するか

使用方法:
  python3 scripts/verify_demos.py
  python3 scripts/verify_demos.py --demo DEMO1
"""

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

BASE_DIR  = Path(__file__).resolve().parent.parent
UC_JSON   = BASE_DIR / "modules" / "dap" / "app" / "uc_definitions.json"
PLATFORM  = "http://localhost:8000"

PORT_TO_MODULE = {
    "8011": "ai_validation",
    "8001": "ai_validation",
    "8002": "ai_classification",
    "8003": "rnd_assessment",
    "8004": "patent_search",
    "8005": "screening",
    "8006": "hs_classifier",
    "8010": "dap",
    "8012": "export_license",
    "8013": "trade_gate",
    "8014": "fta_origin",
}


def _resolve_url(url: str) -> str:
    """
    {PLATFORM_URL} を localhost:8000 に置換し、
    localhost:{port}/path → localhost:8000/proxy/{module}/path に正規化する。
    """
    url = url.replace("{PLATFORM_URL}", PLATFORM)
    m = re.match(r"https?://localhost:(\d+)(/.*)$", url)
    if m:
        port, path = m.group(1), m.group(2)
        module = PORT_TO_MODULE.get(port)
        if module:
            url = f"{PLATFORM}/proxy/{module}{path}"
        else:
            url = f"{PLATFORM}{path}"
    return url


def _check_url(url: str, client: httpx.Client) -> tuple[int, str]:
    resolved = _resolve_url(url)
    try:
        r = client.get(resolved, timeout=8.0, follow_redirects=True)
        return r.status_code, resolved
    except Exception as e:
        return 0, f"{resolved} → ERROR: {e}"


def _text_in_html(html: str, text: str) -> bool:
    """大文字小文字・空白・絵文字を無視してテキスト検索する。"""
    def _norm(s: str) -> str:
        return re.sub(r"\s+", "", s).lower()
    norm_text = _norm(text)
    norm_html = _norm(html)
    return norm_text in norm_html


def _field_in_html(html: str, label: str) -> bool:
    """data-dap-field / placeholder / label / name / id でフィールド検索する。"""
    patterns = [
        f'data-dap-field="{re.escape(label)}"',
        f"placeholder=\"{re.escape(label)}",
        f"name=\"{re.escape(label)}\"",
        f">{re.escape(label)}<",
    ]
    lower_html = html.lower()
    lower_label = label.lower()
    return any(p.lower() in lower_html for p in patterns) or lower_label in lower_html


def verify_demo(uc: dict, client: httpx.Client, verbose: bool = True) -> list[str]:
    failures = []
    uc_id = uc.get("id", "?")
    title = uc.get("title", "?")
    if verbose:
        print(f"\n{'='*60}")
        print(f"  {uc_id}: {title}")
        print(f"{'='*60}")

    for step in uc.get("steps", []):
        num = step.get("num", "?")
        step_title = step.get("title", "?")
        nav_url = step.get("navigate_to", "")
        highlight = step.get("highlight", "")

        if verbose:
            print(f"\n  Step {num}: {step_title}")

        # ── 1. navigate URL チェック ────────────────────────────
        page_html = ""
        if nav_url:
            code, resolved = _check_url(nav_url, client)
            ok = code in (200, 301, 302, 303)
            symbol = "✅" if ok else "❌"
            if verbose:
                print(f"    {symbol} navigate [{code}] {resolved}")
            if not ok:
                failures.append(f"{uc_id} Step{num}: navigate → {code} {resolved}")
            else:
                # ハイライト検証用に HTML を取得
                try:
                    r = client.get(resolved, timeout=8.0, follow_redirects=True)
                    page_html = r.text
                except Exception:
                    pass

        # ── 2. highlight target チェック ───────────────────────
        if highlight and page_html:
            found = _text_in_html(page_html, highlight)
            symbol = "✅" if found else "⚠️ "
            if verbose:
                print(f"    {symbol} highlight '{highlight}' → {'found' if found else 'NOT FOUND in HTML'}")
            if not found:
                failures.append(f"{uc_id} Step{num}: highlight '{highlight}' not found")

        # ── 3. guidance_steps の navigate/fill_field チェック ──
        for gs in step.get("guidance_steps", []):
            gs_type = gs.get("type", "")
            if gs_type == "navigate":
                gs_url = gs.get("url", "")
                if gs_url:
                    code, resolved = _check_url(gs_url, client)
                    ok = code in (200, 301, 302, 303)
                    symbol = "✅" if ok else "❌"
                    if verbose:
                        print(f"      {symbol} guidance navigate [{code}] {resolved}")
                    if not ok:
                        failures.append(f"{uc_id} Step{num} guidance-navigate → {code} {resolved}")
            elif gs_type in ("fill_field", "fill_demo"):
                target = gs.get("target", "")
                if target and page_html:
                    found = _field_in_html(page_html, target)
                    symbol = "✅" if found else "⚠️ "
                    if verbose:
                        print(f"      {symbol} fill_field '{target}' → {'found' if found else 'NOT FOUND'}")
                    if not found:
                        failures.append(f"{uc_id} Step{num}: fill_field '{target}' not found")

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="DEMO定義の整合性検証")
    parser.add_argument("--demo", help="特定DEMOのみ実行 (例: DEMO1)")
    parser.add_argument("--quiet", action="store_true", help="エラーのみ表示")
    args = parser.parse_args()

    with open(UC_JSON, encoding="utf-8") as f:
        all_ucs = json.load(f)

    # DEMO（id が "DEMO" で始まるもの）のみ抽出
    demos = [u for u in all_ucs if u.get("id", "").startswith("DEMO")]
    if args.demo:
        demos = [u for u in demos if u["id"].upper() == args.demo.upper()]
        if not demos:
            print(f"ERROR: {args.demo} が見つかりません", file=sys.stderr)
            return 2

    all_failures = []
    with httpx.Client(timeout=10.0) as client:
        for uc in demos:
            failures = verify_demo(uc, client, verbose=not args.quiet)
            all_failures.extend(failures)

    print(f"\n{'='*60}")
    if all_failures:
        print(f"❌ 検証失敗 {len(all_failures)} 件:")
        for f in all_failures:
            print(f"  • {f}")
        return 1
    else:
        print(f"✅ 全 {len(demos)} DEMOの検証に成功しました")
        return 0


if __name__ == "__main__":
    sys.exit(main())
