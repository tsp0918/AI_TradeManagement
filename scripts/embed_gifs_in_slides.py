#!/usr/bin/env python3
"""
embed_gifs_in_slides.py — スライド07-12 のプレースホルダーをGIFに置き換える

使い方:
  python scripts/embed_gifs_in_slides.py

前提:
  - make_gifs.py が完了して output/gifs/gif_drive_ids.json が存在すること
  - output/slides_url.txt にスライドのURLが保存されていること
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

BASE_DIR   = Path(__file__).resolve().parents[1]
TOKEN_FILE = BASE_DIR / "token.json"
IDS_FILE   = BASE_DIR / "output" / "gifs" / "gif_drive_ids.json"
URL_FILE   = BASE_DIR / "output" / "slides_url.txt"

SCOPES = [
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive",
]

# スライドID と mockupID の対応
SLIDE_MOCKUP_MAP = {
    "slide_07_feat1": "mockup3",   # R&D リスク管理
    "slide_08_feat2": "mockup4",   # 特許検索
    "slide_09_feat3": "mockup2",   # 品目管理
    "slide_10_feat4": "mockup1",   # AI 分類ツール
    "slide_11_feat5": "mockup5",   # AI 取引管理
    "slide_12_feat6": "mockup6",   # AI ユーザー支援
}

# スライド内の画像配置（モックアップブラウザ body 領域）
# create_slides.py の mock_x/mock_y に合わせた値
IMG_X = 5200000   # mock_x + 200000 (padding)
IMG_Y =  850000   # mock_y + 350000 (titlebar高さ)
IMG_W = 3500000
IMG_H = 3500000


def get_presentation_id(url_file: Path) -> str:
    text = url_file.read_text().strip()
    m = re.search(r"/presentation/d/([a-zA-Z0-9_-]+)", text)
    if not m:
        print(f"❌ スライドIDをURLから取得できませんでした: {text}")
        sys.exit(1)
    return m.group(1)


def find_placeholder_elements(slides_service, presentation_id: str) -> dict[str, list[str]]:
    """各スライドにあるプレースホルダーテキストボックスのIDを収集する"""
    prs = slides_service.presentations().get(presentationId=presentation_id).execute()
    result: dict[str, list[str]] = {}

    for slide in prs.get("slides", []):
        sid = slide["objectId"]
        if sid not in SLIDE_MOCKUP_MAP:
            continue
        placeholder_ids = []
        for el in slide.get("pageElements", []):
            shape = el.get("shape", {})
            text_content = ""
            for te in shape.get("text", {}).get("textElements", []):
                text_content += te.get("textRun", {}).get("content", "")
            if "UI プレビュー" in text_content:
                placeholder_ids.append(el["objectId"])
        result[sid] = placeholder_ids

    return result


def main():
    print("=== GIF をスライドに埋め込む ===")

    for f in [TOKEN_FILE, IDS_FILE, URL_FILE]:
        if not f.exists():
            print(f"❌ {f} が見つかりません。")
            sys.exit(1)

    drive_ids: dict[str, str] = json.loads(IDS_FILE.read_text())
    presentation_id = get_presentation_id(URL_FILE)
    print(f"スライドID: {presentation_id}")
    print(f"GIF Drive IDs: {list(drive_ids.keys())}")

    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    slides_service = build("slides", "v1", credentials=creds)

    # プレースホルダーを特定
    print("プレースホルダーを検索中...")
    placeholders = find_placeholder_elements(slides_service, presentation_id)

    requests = []

    for slide_id, mockup_id in SLIDE_MOCKUP_MAP.items():
        fid = drive_ids.get(mockup_id)
        if not fid:
            print(f"  ⚠️  {mockup_id} のDrive IDが見つかりません。スキップ。")
            continue

        img_url = f"https://drive.google.com/uc?export=view&id={fid}"

        # 既存のプレースホルダーを削除
        for ph_id in placeholders.get(slide_id, []):
            requests.append({"deleteObject": {"objectId": ph_id}})
            print(f"  削除: {ph_id} ({slide_id})")

        # GIF画像を挿入
        img_obj_id = f"gif_{slide_id}"
        requests.append({
            "createImage": {
                "objectId": img_obj_id,
                "url": img_url,
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {
                        "width":  {"magnitude": IMG_W, "unit": "EMU"},
                        "height": {"magnitude": IMG_H, "unit": "EMU"},
                    },
                    "transform": {
                        "scaleX": 1, "scaleY": 1,
                        "translateX": IMG_X,
                        "translateY": IMG_Y,
                        "unit": "EMU",
                    },
                },
            }
        })
        print(f"  ✅ GIF挿入: {mockup_id} → {slide_id}")

    if not requests:
        print("挿入するリクエストがありません。")
        sys.exit(0)

    # batchUpdate
    print(f"\n{len(requests)} 件のリクエストを送信中...")
    slides_service.presentations().batchUpdate(
        presentationId=presentation_id,
        body={"requests": requests},
    ).execute()

    url = f"https://docs.google.com/presentation/d/{presentation_id}/edit"
    print(f"\n✅ 完了！スライドを確認してください:")
    print(f"   {url}")


if __name__ == "__main__":
    main()
