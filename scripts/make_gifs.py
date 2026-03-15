#!/usr/bin/env python3
"""
make_gifs.py — LPの各モジュールアニメーションをGIFに変換してGoogle Driveにアップロードする

使い方:
  python scripts/make_gifs.py

出力:
  output/gifs/mockup1.gif ~ mockup6.gif
  output/gifs/gif_drive_ids.json  (Drive file ID マップ)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from PIL import Image
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parents[1]
TOKEN_FILE = BASE_DIR / "token.json"
LP_FILE    = BASE_DIR / "index.html"
GIF_DIR    = BASE_DIR / "output" / "gifs"

SCOPES = [
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive",
]

# ── 各モジュールのキャプチャ設定 ───────────────────────────────────────
# (mockup_id, セクションid, 待機ms, キャプチャ時間ms, フレーム間隔ms, ループ数)
MOCKUP_CONFIG = [
    ("mockup1", "feat4", 500,  10000, 150, 1),   # AI 分類ツール (~10s)
    ("mockup2", "feat3", 500,   9000, 150, 1),   # 品目管理
    ("mockup3", "feat1", 500,  10000, 150, 1),   # R&D リスク管理
    ("mockup4", "feat2", 500,   9000, 150, 1),   # 特許検索
    ("mockup5", "feat5", 500,  10000, 150, 1),   # AI 取引管理
    ("mockup6", "feat6", 500,   9000, 150, 1),   # AI ユーザー支援
]

# モックアップ表示領域のクリップ (x, y, width, height) — ブラウザ内座標
# スライド右側のブラウザモックアップの body 部分に対応
CLIP_W = 520
CLIP_H = 420


def capture_mockup_gif(
    page,
    mockup_id: str,
    section_id: str,
    wait_ms: int,
    capture_ms: int,
    interval_ms: int,
    out_path: Path,
) -> None:
    """指定モックアップのアニメーションをGIFとして保存する"""
    print(f"  キャプチャ開始: #{mockup_id}")

    # セクションへスクロール（アニメーション起動）
    page.evaluate(f"""
        document.getElementById('{section_id}').scrollIntoView({{behavior: 'instant'}});
    """)
    page.wait_for_timeout(wait_ms)

    # モックアップ要素の位置を取得
    el = page.locator(f"#{mockup_id}")
    box = el.bounding_box()
    if not box:
        print(f"  ⚠️  #{mockup_id} が見つかりません。スキップします。")
        return

    clip = {
        "x": box["x"],
        "y": box["y"],
        "width":  min(box["width"],  CLIP_W),
        "height": min(box["height"], CLIP_H),
    }

    # フレームキャプチャ
    frames: list[Image.Image] = []
    elapsed = 0
    while elapsed < capture_ms:
        png = page.screenshot(clip=clip)
        img = Image.open(__import__("io").BytesIO(png)).convert("RGBA")
        frames.append(img)
        page.wait_for_timeout(interval_ms)
        elapsed += interval_ms

    if not frames:
        print(f"  ⚠️  フレームが取得できませんでした: #{mockup_id}")
        return

    # GIF 保存（RGBA→P変換でファイルサイズ最適化）
    out_path.parent.mkdir(parents=True, exist_ok=True)
    first = frames[0].convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=128)
    rest  = [f.convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=128) for f in frames[1:]]

    first.save(
        str(out_path),
        format="GIF",
        save_all=True,
        append_images=rest,
        loop=0,
        duration=interval_ms,
        optimize=True,
    )
    size_kb = out_path.stat().st_size // 1024
    print(f"  ✅ 保存: {out_path.name} ({len(frames)} frames, {size_kb} KB)")


def upload_gif_to_drive(drive_service, gif_path: Path, label: str) -> str:
    """GIFをGoogle Driveにアップロードして file_id を返す"""
    print(f"  Drive アップロード: {gif_path.name}...")
    media = MediaFileUpload(str(gif_path), mimetype="image/gif", resumable=False)
    f = drive_service.files().create(
        body={"name": f"mockup_{label}.gif", "mimeType": "image/gif"},
        media_body=media,
        fields="id",
    ).execute()
    fid = f["id"]
    # 閲覧権限を全員に付与
    drive_service.permissions().create(
        fileId=fid,
        body={"type": "anyone", "role": "reader"},
    ).execute()
    print(f"  ✅ Drive ID: {fid}")
    return fid


def main():
    print("=== LP アニメーション → GIF 変換 ===")

    if not TOKEN_FILE.exists():
        print("❌ token.json が見つかりません。先に create_slides.py を実行してください。")
        sys.exit(1)

    if not LP_FILE.exists():
        print(f"❌ {LP_FILE} が見つかりません。")
        sys.exit(1)

    GIF_DIR.mkdir(parents=True, exist_ok=True)

    # ── Playwright でブラウザ起動 ──────────────────────────────────────
    lp_url = LP_FILE.as_uri()
    print(f"LP を開きます: {lp_url}")

    gif_paths: dict[str, Path] = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        # LP を読み込む
        page.goto(lp_url, wait_until="networkidle")
        page.wait_for_timeout(1000)

        # フォントロード待ち
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(500)

        for (mockup_id, section_id, wait_ms, capture_ms, interval_ms, _) in MOCKUP_CONFIG:
            out_path = GIF_DIR / f"{mockup_id}.gif"
            capture_mockup_gif(
                page, mockup_id, section_id,
                wait_ms, capture_ms, interval_ms, out_path,
            )
            if out_path.exists():
                gif_paths[mockup_id] = out_path

            # 次のキャプチャ前にトップへ戻してアニメーションをリセット
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(600)

        browser.close()

    print(f"\n{len(gif_paths)} 件の GIF を生成しました。")

    # ── Google Drive にアップロード ────────────────────────────────────
    print("\nGoogle Drive にアップロード中...")
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    drive_service = build("drive", "v3", credentials=creds)

    drive_ids: dict[str, str] = {}
    for mockup_id, gif_path in gif_paths.items():
        fid = upload_gif_to_drive(drive_service, gif_path, mockup_id)
        drive_ids[mockup_id] = fid

    # ID マップを保存
    ids_file = GIF_DIR / "gif_drive_ids.json"
    ids_file.write_text(json.dumps(drive_ids, indent=2, ensure_ascii=False))
    print(f"\n✅ Drive ID マップを保存: {ids_file}")

    print("\n=== 完了 ===")
    for mid, fid in drive_ids.items():
        url = f"https://drive.google.com/uc?id={fid}"
        print(f"  {mid}: {url}")

    print("\n次のステップ: python scripts/embed_gifs_in_slides.py を実行して")
    print("スライドのプレースホルダーをGIFに置き換えます。")


if __name__ == "__main__":
    main()
