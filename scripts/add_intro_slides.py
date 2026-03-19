#!/usr/bin/env python3
"""
add_intro_slides.py — 既存プレゼンテーションにイントロスライドを追加する

挿入位置: Hero スライド (index=0) の直後 (insertionIndex=1, 2)

追加する2枚:
  INT-1 (index=1): "制裁の現実" — コンプライアンス違反のビジネスコスト
  INT-2 (index=2): "規制の収斂" — IP × 輸出管理 × 経済安全保障の統合ガバナンス

使い方:
  python scripts/add_intro_slides.py
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ── パス / 認証 ─────────────────────────────────────────────────────
BASE_DIR         = Path(__file__).resolve().parents[1]
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE       = BASE_DIR / "token.json"
SLIDES_URL_FILE  = BASE_DIR / "output" / "slides_url.txt"

SCOPES = [
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive",
]

# ── デザイン定数 ─────────────────────────────────────────────────────
SLIDE_W = 9144000
SLIDE_H = 5143500

C_BG    = {"red": 0.039, "green": 0.055, "blue": 0.102}   # #0A0E1A
C_BG2   = {"red": 0.051, "green": 0.071, "blue": 0.125}   # #0D1220
C_CYAN  = {"red": 0.0,   "green": 0.831, "blue": 1.0}     # #00D4FF
C_GOLD  = {"red": 0.784, "green": 0.663, "blue": 0.431}   # #C8A96E
C_WHITE = {"red": 0.941, "green": 0.957, "blue": 1.0}     # #F0F4FF
C_GRAY  = {"red": 0.533, "green": 0.573, "blue": 0.643}   # #8892A4
C_DARK2 = {"red": 0.067, "green": 0.094, "blue": 0.153}   # #111827
C_RED   = {"red": 0.97,  "green": 0.27,  "blue": 0.27}    # #F74444
C_ORANGE= {"red": 0.99,  "green": 0.55,  "blue": 0.0}     # #FD8C00
C_PURPLE= {"red": 0.55,  "green": 0.35,  "blue": 0.90}    # #8C5AE6

_ID_COUNTER = itertools.count(1000)  # create_slides.py と衝突しないよう1000番台から

def _uid(prefix: str) -> str:
    return f"intro_{prefix}_{next(_ID_COUNTER):05d}"


# ── 認証 ────────────────────────────────────────────────────────────
def get_credentials() -> Credentials:
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds


# ── ヘルパー ─────────────────────────────────────────────────────────
def _bg_rect(sid, color, w=SLIDE_W, h=SLIDE_H, x=0, y=0, obj_id=None):
    eid = obj_id or _uid("bg")
    return [
        {"createShape": {"objectId": eid, "shapeType": "RECTANGLE",
            "elementProperties": {"pageObjectId": sid,
                "size": {"width": {"magnitude": w, "unit": "EMU"},
                         "height": {"magnitude": h, "unit": "EMU"}},
                "transform": {"scaleX": 1, "scaleY": 1,
                              "translateX": x, "translateY": y, "unit": "EMU"}}}},
        {"updateShapeProperties": {"objectId": eid,
            "shapeProperties": {
                "shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": color}}},
                "outline": {"outlineFill": {"solidFill": {"color": {"rgbColor": color}}}}},
            "fields": "shapeBackgroundFill,outline"}},
    ]


def _txt(sid, text, x, y, w, h, pt, color, bold=False, align="START", italic=False):
    eid = _uid("t")
    return [
        {"createShape": {"objectId": eid, "shapeType": "TEXT_BOX",
            "elementProperties": {"pageObjectId": sid,
                "size": {"width": {"magnitude": w, "unit": "EMU"},
                         "height": {"magnitude": h, "unit": "EMU"}},
                "transform": {"scaleX": 1, "scaleY": 1,
                              "translateX": x, "translateY": y, "unit": "EMU"}}}},
        {"insertText": {"objectId": eid, "insertionIndex": 0, "text": text}},
        {"updateTextStyle": {"objectId": eid,
            "style": {"fontSize": {"magnitude": pt, "unit": "PT"},
                      "foregroundColor": {"opaqueColor": {"rgbColor": color}},
                      "bold": bold, "italic": italic,
                      "fontFamily": "Noto Sans JP"},
            "fields": "fontSize,foregroundColor,bold,italic,fontFamily"}},
        {"updateParagraphStyle": {"objectId": eid,
            "style": {"alignment": align},
            "fields": "alignment"}},
    ]


def _line(sid, x, y, w, color):
    return _bg_rect(sid, color, w=w, h=110000, x=x, y=y)


def _card(sid, x, y, w, h, color=None):
    return _bg_rect(sid, color or C_DARK2, w=w, h=h, x=x, y=y)


# ── スライド INT-1: "制裁の現実" ──────────────────────────────────────
def build_int1(sid: str) -> list[dict]:
    """
    コンプライアンス違反のビジネスコスト。
    Applied Materials ($252M, 2026)、Seagate ($300M, 2023)、
    Analog Devices ($3.3M, 2020) の3事例カードを横並びで表示。
    """
    reqs: list[dict] = []

    # 背景
    reqs += _bg_rect(sid, C_BG)

    # 上部 セクションラベル
    reqs += _txt(sid, "INTRODUCTION — WHY THIS MATTERS",
                 457200, 228600, 5000000, 300000, 10, C_CYAN, bold=True)
    reqs += _line(sid, 457200, 500000, 8229600, C_CYAN)

    # タイトル
    reqs += _txt(sid, "輸出管理違反の\nビジネスインパクト",
                 457200, 560000, 7315200, 760000, 34, C_WHITE, bold=True)

    # サブタイトル
    reqs += _txt(sid,
                 "Trade Compliance の失敗は、罰金にとどまらず事業継続・信頼・マーケットアクセスを失う",
                 457200, 1280000, 8229600, 320000, 15, C_GRAY, italic=True)

    # ── カード3枚 (横並び) ───────────────────────────────────────────
    card_w = 2600000
    card_h = 2400000
    gap    = 280000
    card_y = 1680000
    xs = [457200, 457200 + card_w + gap, 457200 + (card_w + gap) * 2]

    # Case 1: Applied Materials
    reqs += _card(sid, xs[0], card_y, card_w, card_h)
    reqs += _bg_rect(sid, C_RED, w=card_w, h=80000, x=xs[0], y=card_y)
    reqs += _txt(sid, "Applied Materials (米国)", xs[0]+160000, card_y+120000,
                 card_w-320000, 280000, 16, C_WHITE, bold=True)
    reqs += _txt(sid, "$252M", xs[0]+160000, card_y+400000,
                 card_w-320000, 400000, 44, C_RED, bold=True)
    reqs += _txt(sid, "BIS史上2位の制裁金（2026年2月）",
                 xs[0]+160000, card_y+800000, card_w-320000, 240000, 12, C_GRAY)
    reqs += _txt(sid,
                 "Entity List掲載の中国企業へ半導体製造装置（イオン注入機）を韓国子会社経由で迂回輸出。"
                 "違反額の2倍（法定最高）。コンプライアンス担当役員は退職処分。",
                 xs[0]+160000, card_y+1080000, card_w-320000, 1100000, 11, C_WHITE)

    # Case 2: Seagate Technology
    reqs += _card(sid, xs[1], card_y, card_w, card_h)
    reqs += _bg_rect(sid, C_ORANGE, w=card_w, h=80000, x=xs[1], y=card_y)
    reqs += _txt(sid, "Seagate Technology (米国)", xs[1]+160000, card_y+120000,
                 card_w-320000, 280000, 16, C_WHITE, bold=True)
    reqs += _txt(sid, "$300M", xs[1]+160000, card_y+400000,
                 card_w-320000, 400000, 44, C_ORANGE, bold=True)
    reqs += _txt(sid, "BIS最大の制裁金（2023年4月）",
                 xs[1]+160000, card_y+800000, card_w-320000, 240000, 12, C_GRAY)
    reqs += _txt(sid,
                 "Huawei向けにHDD約1,290万台を輸出（米国製技術含む）。"
                 "他社が輸出を停止した後も独自に供給継続。外部コンプライアンス監視・年次報告を義務化。",
                 xs[1]+160000, card_y+1080000, card_w-320000, 1100000, 11, C_WHITE)

    # Case 3: AIST研究者（日本事例）
    reqs += _card(sid, xs[2], card_y, card_w, card_h)
    reqs += _bg_rect(sid, C_PURPLE, w=card_w, h=80000, x=xs[2], y=card_y)
    reqs += _txt(sid, "産業技術総合研究所（日本）", xs[2]+160000, card_y+120000,
                 card_w-320000, 280000, 16, C_WHITE, bold=True)
    reqs += _txt(sid, "実刑判決", xs[2]+160000, card_y+400000,
                 card_w-320000, 400000, 44, C_PURPLE, bold=True)
    reqs += _txt(sid, "研究者への刑事訴追（2023年）",
                 xs[2]+160000, card_y+800000, card_w-320000, 240000, 12, C_GRAY)
    reqs += _txt(sid,
                 "フッ化化合物データを中国企業に送信した研究者が不正競争防止法違反で有罪判決。"
                 "みなし輸出・営業秘密管理の強化が大学・研究機関で急務となる契機。",
                 xs[2]+160000, card_y+1080000, card_w-320000, 1100000, 11, C_WHITE)

    # 下部メッセージ
    reqs += _line(sid, 457200, 4200000, 8229600, C_GOLD)
    reqs += _txt(sid,
                 "⚠  違反のコストは「罰金」だけではない —— 取引停止・ライセンス剥奪・役員訴追・市場退出リスク",
                 457200, 4270000, 8229600, 320000, 13, C_GOLD, bold=True, align="CENTER")

    return reqs


# ── スライド INT-2: "規制の収斂" ──────────────────────────────────────
def build_int2(sid: str) -> list[dict]:
    """
    IP × 輸出管理 × 経済安全保障の収斂。
    3フレーム: 日本(ESPA/外為法)、米国(BIS/CHIPS/OISP)、新たな戦場（みなし輸出・希土類）
    """
    reqs: list[dict] = []

    # 背景
    reqs += _bg_rect(sid, C_BG)

    # セクションラベル
    reqs += _txt(sid, "INTRODUCTION — THE NEW REALITY",
                 457200, 228600, 5000000, 300000, 10, C_CYAN, bold=True)
    reqs += _line(sid, 457200, 500000, 8229600, C_CYAN)

    # タイトル
    reqs += _txt(sid, "IP・輸出管理・経済安全保障の収斂",
                 457200, 560000, 8000000, 640000, 34, C_WHITE, bold=True)

    # サブタイトル
    reqs += _txt(sid,
                 "かつて別々の部門が担っていたコンプライアンスが、2020年代に「経済安全保障ガバナンス」として一体化した",
                 457200, 1180000, 8229600, 320000, 14, C_GRAY, italic=True)

    # ── 3カラム ──────────────────────────────────────────────────────
    col_w = 2600000
    col_h = 2640000
    gap   = 272000
    col_y = 1600000
    xs    = [457200, 457200 + col_w + gap, 457200 + (col_w + gap) * 2]

    # 列1: 日本
    reqs += _card(sid, xs[0], col_y, col_w, col_h)
    reqs += _bg_rect(sid, C_CYAN, w=col_w, h=80000, x=xs[0], y=col_y)
    reqs += _txt(sid, "🇯🇵  日本", xs[0]+160000, col_y+120000,
                 col_w-320000, 280000, 18, C_CYAN, bold=True)
    reqs += _txt(sid,
                 "経済安全保障推進法（ESPA 2022）\n"
                 "・サプライチェーン強靱化\n"
                 "・特許出願非公開制度\n"
                 "・セキュリティクリアランス（2025.5施行）\n\n"
                 "外為法改正（2022）\n"
                 "・みなし輸出の明確化\n"
                 "・デュアルユーザー3カテゴリ\n\n"
                 "違反：5年以下の懲役",
                 xs[0]+160000, col_y+430000, col_w-320000, col_h-500000, 11, C_WHITE)

    # 列2: 米国
    reqs += _card(sid, xs[1], col_y, col_w, col_h)
    reqs += _bg_rect(sid, C_RED, w=col_w, h=80000, x=xs[1], y=col_y)
    reqs += _txt(sid, "🇺🇸  米国", xs[1]+160000, col_y+120000,
                 col_w-320000, 280000, 18, C_RED, bold=True)
    reqs += _txt(sid,
                 "BIS / EAR（2022〜2024強化）\n"
                 "・先端半導体・AI・量子を対象\n"
                 "・Entity List / 43か国に拡大\n\n"
                 "CHIPS Act ガードレール\n"
                 "・中国での生産拡大10年禁止\n"
                 "・技術・共同研究の制限\n\n"
                 "OISP（2025.1施行）\n"
                 "・対中アウトバウンド投資規制",
                 xs[1]+160000, col_y+430000, col_w-320000, col_h-500000, 11, C_WHITE)

    # 列3: 新たな戦場
    reqs += _card(sid, xs[2], col_y, col_w, col_h)
    reqs += _bg_rect(sid, C_GOLD, w=col_w, h=80000, x=xs[2], y=col_y)
    reqs += _txt(sid, "⚡  新たな戦場", xs[2]+160000, col_y+120000,
                 col_w-320000, 280000, 18, C_GOLD, bold=True)
    reqs += _txt(sid,
                 "中国レアアース輸出規制（2024〜）\n"
                 "・ガリウム・ゲルマニウム・アンチモン\n"
                 "・0.1%ルール（域外適用）\n\n"
                 "EU デュアルユース規則 2021/821\n"
                 "・EUV・ALD装置・半導体テスト機材\n"
                 "・オランダ（ASML DUV制限）\n\n"
                 "技術主権 vs. グローバル協調\n"
                 "・WEF / Bain / McKinsey が警告",
                 xs[2]+160000, col_y+430000, col_w-320000, col_h-500000, 11, C_WHITE)

    # 下部: 4象限フレームワークへの誘導
    reqs += _line(sid, 457200, 4350000, 8229600, C_CYAN)
    reqs += _txt(sid,
                 "📊  技術ポートフォリオは「技術主権価値 × 規制感度」の4象限で管理する時代へ",
                 457200, 4410000, 8229600, 320000, 13, C_CYAN, bold=True, align="CENTER")

    return reqs


# ── メイン ──────────────────────────────────────────────────────────
def main():
    # プレゼンテーション ID 取得
    url = SLIDES_URL_FILE.read_text(encoding="utf-8").strip()
    presentation_id = url.split("/d/")[1].split("/")[0]
    print(f"Presentation ID: {presentation_id}")

    # 認証
    creds = get_credentials()
    svc = build("slides", "v1", credentials=creds)

    # 現在のスライド一覧を取得（挿入位置確定）
    prs = svc.presentations().get(presentationId=presentation_id).execute()
    slides = prs.get("slides", [])
    print(f"Current slides: {len(slides)}")

    # スライド ID を生成（既存と衝突しない固定名を使用）
    sid_int1 = "intro_slide_sanctions"
    sid_int2 = "intro_slide_convergence"

    # ── スライド作成リクエスト ──────────────────────────────────────
    requests = [
        # INT-1 を index=1 に挿入（Hero の直後）
        {"createSlide": {
            "objectId": sid_int1,
            "insertionIndex": 1,
            "slideLayoutReference": {"predefinedLayout": "BLANK"},
        }},
        # INT-2 を index=2 に挿入（INT-1 の直後）
        {"createSlide": {
            "objectId": sid_int2,
            "insertionIndex": 2,
            "slideLayoutReference": {"predefinedLayout": "BLANK"},
        }},
    ]

    # ── コンテンツリクエスト ────────────────────────────────────────
    requests += build_int1(sid_int1)
    requests += build_int2(sid_int2)

    print(f"Total requests: {len(requests)}")

    # ── API 実行（500件ずつに分割）─────────────────────────────────
    CHUNK = 500
    for i in range(0, len(requests), CHUNK):
        chunk = requests[i:i + CHUNK]
        svc.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": chunk},
        ).execute()
        print(f"  batch {i//CHUNK + 1}: {len(chunk)} requests sent")

    print(f"\n✅ イントロスライド追加完了")
    print(f"   https://docs.google.com/presentation/d/{presentation_id}/edit")


if __name__ == "__main__":
    main()
