#!/usr/bin/env python3
"""
create_slides.py — LPをもとにGoogleスライドを生成してDriveに保存する

使い方:
  python scripts/create_slides.py

初回実行時にブラウザでGoogle認証が開きます。
以降はtoken.jsonを再利用します。
"""
from __future__ import annotations

import itertools
import json
import os
import sys
import time
from pathlib import Path

# グローバルカウンター（オブジェクトID重複を防ぐ）
_ID_COUNTER = itertools.count(1)

def _uid(prefix: str) -> str:
    return f"{prefix}_{next(_ID_COUNTER):05d}"

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ── 定数 ────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parents[1]
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"
VIDEO_FILE = BASE_DIR / "output" / "demo_video.mp4"

SCOPES = [
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive",
]

# スライドサイズ (EMU: 1inch = 914400)
SLIDE_W = 9144000   # 10 inch (16:9)
SLIDE_H = 5143500   # 5.625 inch

# カラーパレット（0〜1 スケール）
C_BG       = {"red": 0.039, "green": 0.055, "blue": 0.102}   # #0A0E1A
C_BG2      = {"red": 0.051, "green": 0.071, "blue": 0.125}   # #0D1220
C_CYAN     = {"red": 0.0,   "green": 0.831, "blue": 1.0}     # #00D4FF
C_GOLD     = {"red": 0.784, "green": 0.663, "blue": 0.431}   # #C8A96E
C_WHITE    = {"red": 0.941, "green": 0.957, "blue": 1.0}     # #F0F4FF
C_GRAY     = {"red": 0.533, "green": 0.573, "blue": 0.643}   # #8892A4
C_DARK2    = {"red": 0.067, "green": 0.094, "blue": 0.153}   # #111827
C_RED_SOFT = {"red": 0.97,  "green": 0.27,  "blue": 0.27}    # #F74444
C_CYAN_DIM = {"red": 0.0,   "green": 0.502, "blue": 0.60}    # dimmed cyan border


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


# ── ヘルパー: リクエスト生成 ─────────────────────────────────────────

def _bg_rect(slide_id: str, color: dict, w=SLIDE_W, h=SLIDE_H, x=0, y=0, obj_id: str | None = None) -> dict:
    """背景矩形を追加するリクエスト"""
    eid = obj_id or _uid("bg")
    return {
        "createShape": {
            "objectId": eid,
            "shapeType": "RECTANGLE",
            "elementProperties": {
                "pageObjectId": slide_id,
                "size": {"width": {"magnitude": w, "unit": "EMU"},
                         "height": {"magnitude": h, "unit": "EMU"}},
                "transform": {"scaleX": 1, "scaleY": 1,
                              "translateX": x, "translateY": y, "unit": "EMU"},
            },
        }
    }, {
        "updateShapeProperties": {
            "objectId": eid,
            "shapeProperties": {
                "shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": color}}},
                "outline": {"outlineFill": {"solidFill": {"color": {"rgbColor": color}}}},
            },
            "fields": "shapeBackgroundFill,outline",
        }
    }


def _text_box(slide_id: str, text: str, x: int, y: int, w: int, h: int,
              font_size: float, color: dict, bold=False, italic=False,
              align="START", obj_id: str | None = None,
              font_family="Noto Sans JP") -> list[dict]:
    """テキストボックスを追加するリクエスト群"""
    eid = obj_id or _uid("txt")
    reqs = [
        {
            "createShape": {
                "objectId": eid,
                "shapeType": "TEXT_BOX",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {"width": {"magnitude": w, "unit": "EMU"},
                             "height": {"magnitude": h, "unit": "EMU"}},
                    "transform": {"scaleX": 1, "scaleY": 1,
                                  "translateX": x, "translateY": y, "unit": "EMU"},
                },
            }
        },
        {
            "insertText": {"objectId": eid, "insertionIndex": 0, "text": text}
        },
        {
            "updateTextStyle": {
                "objectId": eid,
                "style": {
                    "fontSize": {"magnitude": font_size, "unit": "PT"},
                    "foregroundColor": {"opaqueColor": {"rgbColor": color}},
                    "bold": bold,
                    "italic": italic,
                    "fontFamily": font_family,
                },
                "fields": "fontSize,foregroundColor,bold,italic,fontFamily",
            }
        },
        {
            "updateParagraphStyle": {
                "objectId": eid,
                "style": {"alignment": align},
                "fields": "alignment",
            }
        },
    ]
    return reqs


def _accent_line(slide_id: str, x: int, y: int, w: int, color: dict,
                 obj_id: str | None = None) -> list[dict]:
    """アクセントライン（細い矩形）"""
    eid = obj_id or _uid("line")
    h = 127000  # ~0.14inch
    return list(_bg_rect(slide_id, color, w=w, h=h, x=x, y=y, obj_id=eid))


def _card(slide_id: str, x: int, y: int, w: int, h: int,
          obj_id: str | None = None) -> list[dict]:
    """ダークカード背景"""
    eid = obj_id or _uid("card")
    r1, r2 = _bg_rect(slide_id, C_DARK2, w=w, h=h, x=x, y=y, obj_id=eid)
    return [r1, r2]


# ── スライド作成関数 ─────────────────────────────────────────────────

def build_all_requests(slides_service, presentation_id: str,
                       video_drive_id: str | None) -> list[dict]:
    """全スライドの requests リストを生成して返す"""

    # まず既存のスライドIDを取得
    prs = slides_service.presentations().get(presentationId=presentation_id).execute()
    existing_slide_ids = [s["objectId"] for s in prs.get("slides", [])]

    all_requests: list[dict] = []

    # ── 13枚分のslide IDを定義 ──────────────────────────────────────
    slide_ids = [
        "slide_01_hero",
        "slide_02_problem",
        "slide_03_vision",
        "slide_04_solution",
        "slide_05_tech",
        "slide_06_modules",
        "slide_07_feat1",
        "slide_08_feat2",
        "slide_09_feat3",
        "slide_10_feat4",
        "slide_11_feat5",
        "slide_12_feat6",
        "slide_13_cta",
    ]

    # デフォルトスライドを削除
    for sid in existing_slide_ids:
        all_requests.append({"deleteObject": {"objectId": sid}})

    # 全スライドを作成
    for i, sid in enumerate(slide_ids):
        all_requests.append({
            "createSlide": {
                "objectId": sid,
                "insertionIndex": i,
                "slideLayoutReference": {"predefinedLayout": "BLANK"},
            }
        })

    # 全スライドに背景色を設定
    for sid in slide_ids:
        r1, r2 = _bg_rect(sid, C_BG, obj_id=f"bg_{sid}")
        all_requests.extend([r1, r2])

    # ══════════════════════════════════════════════════════════════
    # Slide 01: HERO
    # ══════════════════════════════════════════════════════════════
    sid = "slide_01_hero"
    # タグライン
    all_requests.extend(_text_box(sid, "Export Control × AI Intelligence",
        x=457200, y=400000, w=5500000, h=350000,
        font_size=10, color=C_CYAN, bold=True))
    # メインヘッドライン
    all_requests.extend(_text_box(sid, "貿易コンプライアンスを、",
        x=457200, y=700000, w=7000000, h=600000,
        font_size=40, color=C_WHITE, bold=True))
    all_requests.extend(_text_box(sid, "インテリジェントに。",
        x=457200, y=1260000, w=7000000, h=600000,
        font_size=40, color=C_CYAN, bold=True))
    # サブテキスト
    all_requests.extend(_text_box(sid,
        "輸出規制分類から特許リスク審査まで。\n複雑な法規制対応を AI エージェントがリアルタイムに支援する統合プラットフォーム。",
        x=457200, y=1900000, w=6500000, h=500000,
        font_size=14, color=C_GRAY))
    # アクセントライン
    all_requests.extend(_accent_line(sid, x=457200, y=2500000, w=914400, color=C_CYAN))
    # KPI 4つ
    kpis = [
        ("5+", "統合モジュール"),
        ("3 規制体系", "外為法 / EAR / ワッセナー"),
        ("AI", "エージェント搭載"),
        ("Claude", "API 統合"),
    ]
    kpi_x_start = 457200
    kpi_w = 1800000
    kpi_gap = 200000
    for i, (num, label) in enumerate(kpis):
        kx = kpi_x_start + i * (kpi_w + kpi_gap)
        all_requests.extend(_text_box(sid, num,
            x=kx, y=2800000, w=kpi_w, h=380000,
            font_size=28, color=C_CYAN, bold=True))
        all_requests.extend(_text_box(sid, label,
            x=kx, y=3180000, w=kpi_w, h=250000,
            font_size=10, color=C_GRAY))
    # スライド番号
    all_requests.extend(_text_box(sid, "01 / 13",
        x=8200000, y=4900000, w=700000, h=200000,
        font_size=9, color=C_GRAY, align="END"))

    # ══════════════════════════════════════════════════════════════
    # Slide 02: PROBLEM
    # ══════════════════════════════════════════════════════════════
    sid = "slide_02_problem"
    all_requests.extend(_text_box(sid, "CONCEPT — PROBLEM",
        x=457200, y=300000, w=4000000, h=300000,
        font_size=10, color=C_GOLD, bold=True))
    all_requests.extend(_text_box(sid, "なぜ、今の輸出管理は限界なのか",
        x=457200, y=580000, w=7000000, h=500000,
        font_size=30, color=C_WHITE, bold=True))
    all_requests.extend(_accent_line(sid, x=457200, y=1100000, w=914400, color=C_RED_SOFT))

    all_requests.extend(_text_box(sid,
        "グローバル取引の複雑化・規制強化・AI 技術の急速な民間転用により、輸出規制の対象品目は年々拡大し、\n"
        "担当者の判断負荷は限界に達している。手作業による HS コード分類、EAR / ITAR / 外為法の横断チェック、\n"
        "特許技術の該非判定——これらを個別ツールで処理する時代は終わった。",
        x=457200, y=1300000, w=8200000, h=600000,
        font_size=13, color=C_GRAY))

    problems = [
        ("規制データの断片化", "データベースの分散と更新遅延による\n見落としリスクが高い"),
        ("属人的な知識集中", "熟練担当者への依存と\n知識の不透明性"),
        ("事後的な該非検出", "R&D 段階でのリスク検出が遅れ、\n手戻りコストが発生"),
    ]
    card_w = 2600000
    card_gap = 200000
    card_x_start = 457200
    for i, (title, desc) in enumerate(problems):
        cx = card_x_start + i * (card_w + card_gap)
        all_requests.extend(_card(sid, x=cx, y=2100000, w=card_w, h=1600000))
        all_requests.extend(_text_box(sid, "⚠",
            x=cx + 150000, y=2200000, w=500000, h=400000,
            font_size=22, color=C_RED_SOFT))
        all_requests.extend(_text_box(sid, title,
            x=cx + 150000, y=2580000, w=card_w - 300000, h=350000,
            font_size=15, color=C_WHITE, bold=True))
        all_requests.extend(_text_box(sid, desc,
            x=cx + 150000, y=2900000, w=card_w - 300000, h=600000,
            font_size=11, color=C_GRAY))
    all_requests.extend(_text_box(sid, "02 / 13",
        x=8200000, y=4900000, w=700000, h=200000,
        font_size=9, color=C_GRAY, align="END"))

    # ══════════════════════════════════════════════════════════════
    # Slide 03: VISION
    # ══════════════════════════════════════════════════════════════
    sid = "slide_03_vision"
    all_requests.extend(_text_box(sid, "CONCEPT — VISION",
        x=457200, y=300000, w=4000000, h=300000,
        font_size=10, color=C_GOLD, bold=True))
    all_requests.extend(_text_box(sid, "あるべき姿：統合された AI コンプライアンス基盤",
        x=457200, y=580000, w=8200000, h=500000,
        font_size=28, color=C_WHITE, bold=True))
    all_requests.extend(_accent_line(sid, x=457200, y=1100000, w=914400, color=C_CYAN))

    all_requests.extend(_text_box(sid,
        "すべての規制判断プロセスをひとつのプラットフォームに統合し、AI が文脈を読み、\n"
        "エージェントが自律的に判断を補佐する。人間の専門家が本来注力すべき\n"
        "高度な意思決定に集中できる環境を実現する。",
        x=457200, y=1300000, w=8200000, h=600000,
        font_size=13, color=C_GRAY))

    visions = [
        ("統合知識グラフ", "複数規制体系を横断する統合知識グラフ\nによる判断支援"),
        ("根拠の透明性", "判定根拠を文章で提示し、\nブラックボックスを排除"),
        ("一気通貫トレーサビリティ", "R&D 起点から取引完結まで\n証跡連鎖で追跡可能"),
    ]
    for i, (title, desc) in enumerate(visions):
        cx = card_x_start + i * (card_w + card_gap)
        all_requests.extend(_card(sid, x=cx, y=2100000, w=card_w, h=1600000))
        all_requests.extend(_text_box(sid, "✦",
            x=cx + 150000, y=2200000, w=500000, h=400000,
            font_size=22, color=C_CYAN))
        all_requests.extend(_text_box(sid, title,
            x=cx + 150000, y=2580000, w=card_w - 300000, h=350000,
            font_size=15, color=C_WHITE, bold=True))
        all_requests.extend(_text_box(sid, desc,
            x=cx + 150000, y=2900000, w=card_w - 300000, h=600000,
            font_size=11, color=C_GRAY))
    all_requests.extend(_text_box(sid, "03 / 13",
        x=8200000, y=4900000, w=700000, h=200000,
        font_size=9, color=C_GRAY, align="END"))

    # ══════════════════════════════════════════════════════════════
    # Slide 04: SOLUTION
    # ══════════════════════════════════════════════════════════════
    sid = "slide_04_solution"
    all_requests.extend(_text_box(sid, "SOLUTION",
        x=457200, y=300000, w=4000000, h=300000,
        font_size=10, color=C_GOLD, bold=True))
    all_requests.extend(_text_box(sid, "AI エージェント × 規制データベース × 統合ワークフロー",
        x=457200, y=580000, w=8200000, h=500000,
        font_size=24, color=C_WHITE, bold=True))
    all_requests.extend(_text_box(sid,
        "単なる検索ツールではない。分類・審査・スクリーニングを一気通貫で処理する AI プラットフォーム。",
        x=457200, y=1100000, w=8200000, h=350000,
        font_size=13, color=C_GRAY))

    solutions = [
        ("🧠", "インテリジェント分類",
         "HS コード・ECCN の AI 自動分類。\n複数国の規制体系を横断的に参照し、\n判定根拠とともに分類候補を提示。\n担当者の判断を補佐し、精度と説明責任を両立。"),
        ("⚡", "リアルタイム・スクリーニング",
         "取引先・品目・仕向地をリアルタイムに\n制裁リスト・エンティティリストと照合。\nWebhook 連携対応で既存ワークフローへ\nシームレスに統合。"),
        ("🔬", "R&D リスク審査",
         "特許データと輸出規制マトリクスを照合し、\n研究・開発段階での該非リスクを早期検出。\n証跡連鎖により R&D から品目管理・取引審査まで\n追跡可能。"),
    ]
    sol_w = 2600000
    sol_gap = 200000
    for i, (icon, title, desc) in enumerate(solutions):
        cx = 457200 + i * (sol_w + sol_gap)
        all_requests.extend(_card(sid, x=cx, y=1600000, w=sol_w, h=2900000))
        all_requests.extend(_text_box(sid, icon,
            x=cx + 200000, y=1750000, w=sol_w - 400000, h=500000,
            font_size=30, color=C_CYAN))
        all_requests.extend(_text_box(sid, title,
            x=cx + 200000, y=2250000, w=sol_w - 400000, h=400000,
            font_size=16, color=C_WHITE, bold=True))
        all_requests.extend(_text_box(sid, desc,
            x=cx + 200000, y=2650000, w=sol_w - 400000, h=1600000,
            font_size=11, color=C_GRAY))
    all_requests.extend(_text_box(sid, "04 / 13",
        x=8200000, y=4900000, w=700000, h=200000,
        font_size=9, color=C_GRAY, align="END"))

    # ══════════════════════════════════════════════════════════════
    # Slide 05: TECHNOLOGY ARCHITECTURE
    # ══════════════════════════════════════════════════════════════
    sid = "slide_05_tech"
    all_requests.extend(_text_box(sid, "TECHNOLOGY",
        x=457200, y=300000, w=4000000, h=300000,
        font_size=10, color=C_GOLD, bold=True))
    all_requests.extend(_text_box(sid, "堅牢なアーキテクチャが支える、エンタープライズグレードの信頼性",
        x=457200, y=580000, w=8200000, h=500000,
        font_size=24, color=C_WHITE, bold=True))

    layers = [
        ("🤖", "AI Agent Layer", "Claude API / Multi-turn Conversation / 判定根拠生成"),
        ("⚙️", "Application Layer", "FastAPI / Python / Jinja2 / Modular Microservices (6 modules)"),
        ("🗄️", "Data & ORM Layer", "SQLAlchemy 2.x / PostgreSQL / SQLite / FAISS Vector Index"),
        ("📡", "Compliance Data Sources", "外為法 / EAR-ECCN / ワッセナー / J-PlatPat / Google Patents"),
    ]
    layer_h = 650000
    layer_gap = 80000
    layer_w = 8200000
    for i, (icon, name, tech) in enumerate(layers):
        ly = 1300000 + i * (layer_h + layer_gap)
        all_requests.extend(_card(sid, x=457200, y=ly, w=layer_w, h=layer_h))
        all_requests.extend(_text_box(sid, icon,
            x=600000, y=ly + 130000, w=500000, h=400000,
            font_size=20, color=C_CYAN))
        all_requests.extend(_text_box(sid, name,
            x=1200000, y=ly + 100000, w=2800000, h=320000,
            font_size=15, color=C_WHITE, bold=True))
        all_requests.extend(_text_box(sid, tech,
            x=1200000, y=ly + 370000, w=5000000, h=230000,
            font_size=10, color=C_GRAY))
        # 左ボーダー
        r1, r2 = _bg_rect(sid, C_CYAN, w=60000, h=layer_h, x=457200, y=ly)
        all_requests.extend([r1, r2])

    # Tech badges
    badges = ["FastAPI", "Python", "SQLAlchemy", "FAISS", "Claude API",
              "PostgreSQL", "Jinja2", "REST API", "J-PlatPat", "Google Patents"]
    bx = 457200
    by = 4700000
    for b in badges:
        bw = len(b) * 80000 + 300000
        all_requests.extend(_card(sid, x=bx, y=by, w=bw, h=280000))
        all_requests.extend(_text_box(sid, b,
            x=bx + 100000, y=by + 50000, w=bw - 200000, h=200000,
            font_size=9, color=C_CYAN))
        bx += bw + 100000
        if bx > 8500000:
            bx = 457200
    all_requests.extend(_text_box(sid, "05 / 13",
        x=8200000, y=4900000, w=700000, h=200000,
        font_size=9, color=C_GRAY, align="END"))

    # ══════════════════════════════════════════════════════════════
    # Slide 06: 6 MODULES OVERVIEW
    # ══════════════════════════════════════════════════════════════
    sid = "slide_06_modules"
    all_requests.extend(_text_box(sid, "FEATURES",
        x=457200, y=300000, w=4000000, h=300000,
        font_size=10, color=C_GOLD, bold=True))
    all_requests.extend(_text_box(sid, "6 つの統合モジュール",
        x=457200, y=580000, w=8200000, h=500000,
        font_size=32, color=C_WHITE, bold=True))

    modules = [
        ("🔬", "R&D リスク管理", "port 8003", "開発初期段階から該非リスクを検出し、\n証跡付きで後工程へ引き継ぐ"),
        ("📄", "特許検索", "port 8004", "J-PlatPat × Google Patents で実特許を収集し、\nAI 判定の参照データセットに統合"),
        ("📦", "品目管理", "port 8002", "HS / ECCN を紐付けた品目マスターを管理し、\nR&D 審査から AI 判定依頼まで一貫運用"),
        ("🧠", "AI 分類ツール", "port 8002", "品目情報を入力するだけで HS・ECCN を自動推定。\n信頼度スコアと根拠テキストを提示"),
        ("⚖️", "AI 取引管理", "port 8001", "取引先・品目・仕向地をスクリーニングし、\nRND → UI → TX の審査連鎖を可視化"),
        ("💬", "AI ユーザー支援", "port 8010", "DAP が各業務画面で AI ガイダンスを提供し、\n担当者の入力品質と審査精度を底上げ"),
    ]
    mod_w = 2700000
    mod_h = 1200000
    mod_gap_x = 200000
    mod_gap_y = 180000
    for i, (icon, title, port, desc) in enumerate(modules):
        row, col = divmod(i, 3)
        mx = 457200 + col * (mod_w + mod_gap_x)
        my = 1300000 + row * (mod_h + mod_gap_y)
        all_requests.extend(_card(sid, x=mx, y=my, w=mod_w, h=mod_h))
        all_requests.extend(_text_box(sid, icon,
            x=mx + 150000, y=my + 100000, w=500000, h=400000,
            font_size=22, color=C_CYAN))
        all_requests.extend(_text_box(sid, title,
            x=mx + 680000, y=my + 120000, w=mod_w - 850000, h=320000,
            font_size=14, color=C_WHITE, bold=True))
        all_requests.extend(_text_box(sid, port,
            x=mx + 680000, y=my + 420000, w=mod_w - 850000, h=200000,
            font_size=9, color=C_CYAN))
        all_requests.extend(_text_box(sid, desc,
            x=mx + 150000, y=my + 650000, w=mod_w - 300000, h=480000,
            font_size=10, color=C_GRAY))
    all_requests.extend(_text_box(sid, "06 / 13",
        x=8200000, y=4900000, w=700000, h=200000,
        font_size=9, color=C_GRAY, align="END"))

    # ══════════════════════════════════════════════════════════════
    # Slides 07-12: 各モジュール詳細
    # ══════════════════════════════════════════════════════════════
    feature_slides = [
        ("slide_07_feat1", "07", "FEATURE 01", "R&D リスク管理",
         "研究・開発の最初期段階から該非リスクを可視化。\nプロジェクト情報と技術仕様を輸出規制マトリクスと照合し、\nリスクスコア・アラート・証跡を自動生成します。\n判定結果はそのまま後工程へ連鎖引継ぎ可能です。",
         ["輸出規制マトリクス（232 件）との FAISS セマンティック照合",
          "R&D 案件ごとに RND 番号を付番し証跡を管理",
          "判定結果を品目管理・取引審査へワンクリックで引継ぎ"],
         "localhost:8003 / R&D リスク管理", "🔬"),
        ("slide_08_feat2", "08", "FEATURE 02", "特許検索",
         "J-PlatPat API と Google Patents を統合した特許データベース。\nセマンティック検索で技術的に近い特許を発見し、\nIPC コードと規制マトリクスを自動照合。\n実特許データを AI 判定の参照データセットに継続的に蓄積します。",
         ["J-PlatPat API 連携で JP 特許の書誌情報を自動取得",
          "Google Patents から IPC・要約・請求項をスクレイプ収集",
          "実特許を合成データと統合して FAISS インデックスに追加"],
         "localhost:8004 / 特許検索", "📄"),
        ("slide_09_feat3", "09", "FEATURE 03", "品目管理",
         "輸出品目を一元管理するマスターデータベース。\nHS コード・ECCN・用途概要を紐付け、\nR&D 審査で確定したリスク情報をそのまま引き継いで品目を登録。\nAI 判定依頼のコンテキスト充足度をチェックしながら承認フローを進めます。",
         ["R&D 案件から品目へのワンクリック昇格フロー",
          "HS コード 6 桁・ECCN の紐付けと管理",
          "AI 判定依頼前のコンテキスト充足度ガイド"],
         "localhost:8002 / 品目管理", "📦"),
        ("slide_10_feat4", "10", "FEATURE 04", "AI 分類ツール",
         "品目名・用途概要を入力するだけで、\nHS コードと ECCN を AI が自動推定。\n複数の規制体系を横断参照し、\n信頼度スコアと判定根拠テキストをセットで提示。\n品目管理へそのまま反映できます。",
         ["HS コード 6 桁・ECCN を同時推定",
          "日本・米国・EU・中国の国別コード対応表",
          "信頼度スコアと根拠テキストをセットで出力"],
         "localhost:8002 / AI 分類", "🧠"),
        ("slide_11_feat5", "11", "FEATURE 05", "AI 取引管理",
         "輸出取引のスクリーニングと審査フローを一元管理。\n取引先・品目・仕向地をエンティティリストと照合し、\nリスクアラートを即時発報。\nR&D・品目管理からの連鎖証跡（RND → UI → TX）を一画面で可視化します。",
         ["FAISS ベクトル検索による高精度エンティティ照合",
          "CSV 一括インポート対応（10,000 件規模）",
          "RND → UI → TX の審査連鎖を証跡として管理"],
         "localhost:8001 / AI 取引管理", "⚖️"),
        ("slide_12_feat6", "12", "FEATURE 06", "AI ユーザー支援",
         "デジタルアダプションプラットフォーム（DAP）が\n各業務画面で AI ガイダンスを提供。\n担当者が迷いやすいポイントでインタラクティブなヒントを表示し、\n入力品質と審査精度を底上げします。",
         ["業務フォームへのリアルタイム AI コーチング",
          "フォーム入力値の充足度チェックと補完提案",
          "チャット形式で追加質問・根拠確認が可能"],
         "DAP — AI ユーザー支援", "💬"),
    ]

    for (sid, num, feat_label, title, desc, bullets, url, icon) in feature_slides:
        all_requests.extend(_text_box(sid, feat_label,
            x=457200, y=300000, w=3000000, h=300000,
            font_size=10, color=C_GOLD, bold=True))
        all_requests.extend(_text_box(sid, f"{icon}  {title}",
            x=457200, y=580000, w=5000000, h=500000,
            font_size=28, color=C_WHITE, bold=True))
        all_requests.extend(_accent_line(sid, x=457200, y=1100000, w=914400, color=C_CYAN))

        # 説明文
        all_requests.extend(_text_box(sid, desc,
            x=457200, y=1250000, w=4400000, h=1400000,
            font_size=12, color=C_GRAY))

        # 箇条書き
        bullet_text = "\n".join(f"• {b}" for b in bullets)
        all_requests.extend(_card(sid, x=457200, y=2700000, w=4400000, h=1600000))
        all_requests.extend(_text_box(sid, bullet_text,
            x=650000, y=2850000, w=4000000, h=1300000,
            font_size=12, color=C_WHITE))

        # 右側: ブラウザモックアップ
        mock_x = 5000000
        mock_y = 500000
        mock_w = 3900000
        mock_h = 3900000
        all_requests.extend(_card(sid, x=mock_x, y=mock_y, w=mock_w, h=mock_h))
        # タイトルバー
        r1, r2 = _bg_rect(sid, C_DARK2, w=mock_w, h=350000, x=mock_x, y=mock_y)
        all_requests.extend([r1, r2])
        # ドット
        for di, dc in enumerate([C_RED_SOFT, C_GOLD, {"red": 0.3, "green": 0.8, "blue": 0.3}]):
            dx = mock_x + 150000 + di * 250000
            r1, r2 = _bg_rect(sid, dc, w=120000, h=120000, x=dx, y=mock_y + 115000)
            all_requests.extend([r1, r2])
        # URL バー
        all_requests.extend(_text_box(sid, url,
            x=mock_x + 850000, y=mock_y + 80000, w=mock_w - 1000000, h=200000,
            font_size=9, color=C_GRAY))
        # コンテンツ領域のプレースホルダー
        all_requests.extend(_text_box(sid, f"[ {title} — UI プレビュー ]",
            x=mock_x + 200000, y=mock_y + 500000, w=mock_w - 400000, h=3200000,
            font_size=13, color={"red": 0.3, "green": 0.4, "blue": 0.5},
            align="CENTER"))

        all_requests.extend(_text_box(sid, f"{num} / 13",
            x=8200000, y=4900000, w=700000, h=200000,
            font_size=9, color=C_GRAY, align="END"))

    # ══════════════════════════════════════════════════════════════
    # Slide 13: CTA + Video
    # ══════════════════════════════════════════════════════════════
    sid = "slide_13_cta"
    all_requests.extend(_text_box(sid, "CONTACT",
        x=457200, y=350000, w=4000000, h=300000,
        font_size=10, color=C_GOLD, bold=True))
    all_requests.extend(_text_box(sid, "輸出コンプライアンスの未来を、\n今すぐ体験する",
        x=457200, y=630000, w=4800000, h=900000,
        font_size=28, color=C_WHITE, bold=True))
    all_requests.extend(_accent_line(sid, x=457200, y=1600000, w=914400, color=C_CYAN))
    all_requests.extend(_text_box(sid,
        "デモ環境へのアクセス、技術仕様の詳細、導入相談はこちらから。",
        x=457200, y=1800000, w=4500000, h=350000,
        font_size=13, color=C_GRAY))

    # CTAボタン風テキスト
    r1, r2 = _bg_rect(sid, C_CYAN, w=2000000, h=380000, x=457200, y=2300000)
    all_requests.extend([r1, r2])
    all_requests.extend(_text_box(sid, "お問い合わせ / デモ申請",
        x=457200, y=2360000, w=2000000, h=300000,
        font_size=13, color=C_BG, bold=True, align="CENTER"))

    r1, r2 = _bg_rect(sid, C_DARK2, w=2000000, h=380000, x=2600000, y=2300000)
    all_requests.extend([r1, r2])
    all_requests.extend(_text_box(sid, "技術ドキュメントを見る",
        x=2600000, y=2360000, w=2000000, h=300000,
        font_size=13, color=C_WHITE, bold=True, align="CENTER"))

    # 動画エリア（右側）
    if video_drive_id:
        all_requests.append({
            "createVideo": {
                "objectId": f"video_{sid}",
                "elementProperties": {
                    "pageObjectId": sid,
                    "size": {"width": {"magnitude": 3800000, "unit": "EMU"},
                             "height": {"magnitude": 2700000, "unit": "EMU"}},
                    "transform": {"scaleX": 1, "scaleY": 1,
                                  "translateX": 5000000, "translateY": 800000,
                                  "unit": "EMU"},
                },
                "source": "DRIVE",
                "id": video_drive_id,
            }
        })
    else:
        # 動画なしの場合はプレースホルダー
        all_requests.extend(_card(sid, x=5000000, y=800000, w=3800000, h=2700000))
        all_requests.extend(_text_box(sid, "🎬  Demo Video\n(output/demo_video.mp4)",
            x=5000000, y=1700000, w=3800000, h=800000,
            font_size=14, color=C_GRAY, align="CENTER"))

    # フッター
    all_requests.extend(_text_box(sid, "[PLATFORM NAME]  —  AI Trade Compliance Platform",
        x=457200, y=4750000, w=7000000, h=250000,
        font_size=10, color=C_GRAY))
    all_requests.extend(_text_box(sid, "13 / 13",
        x=8200000, y=4900000, w=700000, h=200000,
        font_size=9, color=C_GRAY, align="END"))

    return all_requests


# ── メイン ───────────────────────────────────────────────────────────

def main():
    print("=== Google Slides 自動生成 ===")
    print(f"credentials: {CREDENTIALS_FILE}")

    if not CREDENTIALS_FILE.exists():
        print("❌ credentials.json が見つかりません。")
        sys.exit(1)

    creds = get_credentials()
    slides_service = build("slides", "v1", credentials=creds)
    drive_service  = build("drive",  "v3", credentials=creds)

    # ── 動画を Google Drive にアップロード ────────────────────────
    video_drive_id = None
    if VIDEO_FILE.exists():
        print(f"動画をアップロード中: {VIDEO_FILE} ({VIDEO_FILE.stat().st_size // 1024 // 1024} MB)...")
        media = MediaFileUpload(str(VIDEO_FILE), mimetype="video/mp4", resumable=True)
        file_metadata = {"name": "AI_TradeManagement_Demo.mp4", "mimeType": "video/mp4"}
        uploaded = drive_service.files().create(
            body=file_metadata, media_body=media, fields="id"
        ).execute()
        video_drive_id = uploaded.get("id")
        # 共有設定（anyone with link can view）
        drive_service.permissions().create(
            fileId=video_drive_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()
        print(f"✅ 動画アップロード完了: drive file id = {video_drive_id}")
    else:
        print(f"⚠️  動画ファイルが見つかりません: {VIDEO_FILE}")
        print("   スライド13は動画なしで作成します。")

    # ── プレゼンテーション作成 ────────────────────────────────────
    print("プレゼンテーションを作成中...")
    prs = slides_service.presentations().create(body={
        "title": "AI Trade Compliance Platform — Product Deck",
        "pageSize": {
            "width":  {"magnitude": SLIDE_W, "unit": "EMU"},
            "height": {"magnitude": SLIDE_H, "unit": "EMU"},
        },
    }).execute()
    presentation_id = prs["presentationId"]
    print(f"✅ プレゼンテーション作成: {presentation_id}")

    # ── スライドリクエストを生成してバッチ実行 ─────────────────────
    print("スライドを構築中...")
    all_requests = build_all_requests(slides_service, presentation_id, video_drive_id)

    # 500リクエスト単位でバッチ送信（API制限対策）
    chunk_size = 500
    total = len(all_requests)
    print(f"リクエスト総数: {total}")
    for i in range(0, total, chunk_size):
        chunk = all_requests[i:i+chunk_size]
        slides_service.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": chunk},
        ).execute()
        print(f"  送信済み: {min(i+chunk_size, total)}/{total}")

    # ── 共有リンクを取得 ──────────────────────────────────────────
    drive_service.permissions().create(
        fileId=presentation_id,
        body={"type": "anyone", "role": "reader"},
    ).execute()

    url = f"https://docs.google.com/presentation/d/{presentation_id}/edit"
    print()
    print("=" * 60)
    print("✅ 完了！Google スライドを作成しました。")
    print(f"URL: {url}")
    print("=" * 60)

    # URL をファイルに保存
    out_path = BASE_DIR / "output" / "slides_url.txt"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(url + "\n")
    print(f"URL を保存しました: {out_path}")


if __name__ == "__main__":
    main()
