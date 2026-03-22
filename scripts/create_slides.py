#!/usr/bin/env python3
"""
create_slides.py — AI Trade Compliance Management 8スライドデッキを Google Slides に生成

スライド構成:
  01. 輸出管理における課題
  02. 制裁金の発動（AMATの事例）
  03. 安全保障貿易管理における AI 活用の難易度
  04. AI Trade Compliance Management（プロダクト画面）
  05. ソリューションの全体概要一枚絵
  06. ソリューションの技術的優位性
  07. レポーティング、ダッシュボーディング
  08. 各モジュールの詳細（文章＋イメージ動画）

使い方:
  python scripts/create_slides.py

初回実行時にブラウザで Google 認証が開きます。
以降は token.json を再利用します。
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
SLIDE_W = 9144000   # 10 inch  (16:9)
SLIDE_H = 5143500   # 5.625 inch

# ── カラーパレット（0〜1 スケール） ────────────────────────────────
C_BG       = {"red": 0.031, "green": 0.055, "blue": 0.102}   # #080D1A
C_BG2      = {"red": 0.051, "green": 0.071, "blue": 0.125}   # #0D1220
C_DARK2    = {"red": 0.067, "green": 0.094, "blue": 0.153}   # #111827
C_BLUE     = {"red": 0.039, "green": 0.424, "blue": 1.0}     # #0A6CFF
C_BLUE_DIM = {"red": 0.376, "green": 0.643, "blue": 0.980}   # #60A5FA
C_CYAN     = {"red": 0.0,   "green": 0.831, "blue": 1.0}     # #00D4FF
C_GOLD     = {"red": 0.784, "green": 0.663, "blue": 0.431}   # #C8A96E
C_WHITE    = {"red": 0.941, "green": 0.957, "blue": 1.0}     # #F0F4FF
C_GRAY     = {"red": 0.533, "green": 0.573, "blue": 0.643}   # #8892A4
C_GRAY_DIM = {"red": 0.176, "green": 0.216, "blue": 0.282}   # #2D3748
C_GREEN    = {"red": 0.133, "green": 0.773, "blue": 0.369}   # #22C55E
C_GREEN_DIM= {"red": 0.290, "green": 0.855, "blue": 0.502}   # #4ADE80
C_RED_SOFT = {"red": 0.969, "green": 0.267, "blue": 0.267}   # #F74444
C_RED_DIM  = {"red": 0.969, "green": 0.529, "blue": 0.529}   # #F87171
C_PURPLE   = {"red": 0.655, "green": 0.545, "blue": 0.980}   # #A78BFA
C_AMBER    = {"red": 0.984, "green": 0.749, "blue": 0.141}   # #FBBF24


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

def _bg_rect(slide_id: str, color: dict, w=SLIDE_W, h=SLIDE_H,
             x=0, y=0, obj_id: str | None = None) -> tuple[dict, dict]:
    """背景矩形を追加するリクエスト"""
    eid = obj_id or _uid("bg")
    return (
        {
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
        },
        {
            "updateShapeProperties": {
                "objectId": eid,
                "shapeProperties": {
                    "shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": color}}},
                    "outline": {"outlineFill": {"solidFill": {"color": {"rgbColor": color}}}},
                },
                "fields": "shapeBackgroundFill,outline",
            }
        },
    )


def _text_box(slide_id: str, text: str, x: int, y: int, w: int, h: int,
              font_size: float, color: dict, bold=False, italic=False,
              align="START", obj_id: str | None = None,
              font_family="Noto Sans JP") -> list[dict]:
    """テキストボックスを追加するリクエスト群"""
    eid = obj_id or _uid("txt")
    return [
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
        {"insertText": {"objectId": eid, "insertionIndex": 0, "text": text}},
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
                "style": {
                    "alignment": align,
                    "lineSpacing": 115,
                    "spaceAbove": {"magnitude": 0, "unit": "PT"},
                    "spaceBelow": {"magnitude": 0, "unit": "PT"},
                },
                "fields": "alignment,lineSpacing,spaceAbove,spaceBelow",
            }
        },
    ]


def _accent_bar(slide_id: str, x: int, y: int, h: int = 5143500,
                w: int = 152400, color: dict | None = None) -> list[dict]:
    """左端アクセントバー（縦）"""
    c = color or C_BLUE
    r1, r2 = _bg_rect(slide_id, c, w=w, h=h, x=x, y=y, obj_id=_uid("accent"))
    return [r1, r2]


def _h_line(slide_id: str, x: int, y: int, w: int,
            color: dict | None = None, thickness: int = 57150) -> list[dict]:
    """水平アクセントライン"""
    c = color or C_BLUE
    r1, r2 = _bg_rect(slide_id, c, w=w, h=thickness, x=x, y=y, obj_id=_uid("hline"))
    return [r1, r2]


def _card(slide_id: str, x: int, y: int, w: int, h: int,
          color: dict | None = None, obj_id: str | None = None) -> list[dict]:
    """カード背景（角丸は API 非対応のため矩形で代替）"""
    c = color or C_BG2
    r1, r2 = _bg_rect(slide_id, c, w=w, h=h, x=x, y=y, obj_id=obj_id or _uid("card"))
    return [r1, r2]


def _slide_footer(sid: str, num: str) -> list[dict]:
    """フッター: ブランド名 + スライド番号"""
    reqs = []
    reqs.extend(_text_box(sid, "AI TRADE COMPLIANCE MANAGEMENT",
        x=457200, y=4850000, w=5000000, h=200000,
        font_size=7, color=C_GRAY_DIM, bold=True, align="START"))
    reqs.extend(_text_box(sid, num,
        x=8200000, y=4850000, w=700000, h=200000,
        font_size=8, color=C_GRAY_DIM, align="END"))
    return reqs


def _slide_header(sid: str, label: str, title: str, headline: str,
                  label_color: dict | None = None) -> list[dict]:
    """
    全スライド共通ヘッダー:
    左端アクセントバー（57px幅）+ ラベル + タイトル + ヘッドライン + 水平区切り線
    """
    lc = label_color or C_BLUE
    reqs = []

    # 左アクセントバー（上部のみ: y=250000〜y=1200000 程度）
    reqs.extend(_accent_bar(sid, x=228600, y=250000, h=850000, w=76200, color=lc))

    # ラベル（例: "CHALLENGE — 課題提起"）
    reqs.extend(_text_box(sid, label,
        x=457200, y=280000, w=5000000, h=220000,
        font_size=8, color=lc, bold=True))

    # タイトル
    reqs.extend(_text_box(sid, title,
        x=457200, y=490000, w=8400000, h=450000,
        font_size=26, color=C_WHITE, bold=True))

    # ヘッドライン
    reqs.extend(_text_box(sid, headline,
        x=457200, y=920000, w=8400000, h=280000,
        font_size=11, color=C_GRAY))

    # 水平区切り線
    reqs.extend(_h_line(sid, x=457200, y=1230000, w=8229600, thickness=38100, color=lc))

    return reqs


# ═══════════════════════════════════════════════════════════════
# スライド別ビルダー
# ═══════════════════════════════════════════════════════════════

def _build_s01(sid: str) -> list[dict]:
    """Slide 01 — 輸出管理における課題"""
    reqs = []
    reqs.extend(_slide_header(sid,
        label="CHALLENGE — 課題提起",
        title="輸出管理における課題",
        headline="「担当者が退職したら、誰も判定できなくなる」——日本の製造業・研究機関が直面する輸出管理の構造問題",
        label_color=C_BLUE))

    # Stat row (4 items)
    stats = [
        ("数年",     "専門人材育成に\n要する期間"),
        ("数十万円〜","外部委託コスト\n（1件あたり）"),
        ("頻繁",     "外為法・輸出令の\n改正ペース"),
        ("三重",     "違反時の制裁層\n刑事罰・行政処分・信頼失墜"),
    ]
    stat_w, stat_h, stat_gap = 1900000, 600000, 100000
    sx = 457200
    for i, (num, label) in enumerate(stats):
        cx = sx + i * (stat_w + stat_gap)
        reqs.extend(_card(sid, x=cx, y=1320000, w=stat_w, h=stat_h, color=C_BG2))
        reqs.extend(_text_box(sid, num,
            x=cx + 80000, y=1370000, w=stat_w - 160000, h=320000,
            font_size=22, color=C_BLUE, bold=True, align="CENTER"))
        reqs.extend(_text_box(sid, label,
            x=cx + 80000, y=1680000, w=stat_w - 160000, h=220000,
            font_size=9, color=C_GRAY, align="CENTER"))

    # 3 problem cards
    cards = [
        ("👤", "人材リスク", "専門知識の属人化",
         "該非判定の専門人材は国内でも希少。担当者の退職・異動で\n組織の輸出管理機能が一夜にして消滅するリスクを\n多くの企業が抱える。"),
        ("⏱", "スピードリスク", "外部委託の遅延・高コスト",
         "外部専門家への委託は数週間〜数ヶ月・数十万円以上。\n急ぎの海外案件への対応不能が商機の喪失に直結。\n内製化しても習熟に数年を要する。"),
        ("🕳", "規制カバレッジ", "キャッチオール規制の見落とし",
         "リスト規制外の汎用品でも「用途・仕向地・取引パターン」\nによってキャッチオール規制（輸出令第4条・第4条の2）が発動。\n自己判断の難しさが構造的な見落としを生む。"),
    ]
    card_w, card_h, card_gap = 2700000, 1700000, 200000
    cx = 457200
    for i, (icon, tag, title, body) in enumerate(cards):
        x = cx + i * (card_w + card_gap)
        reqs.extend(_card(sid, x=x, y=2050000, w=card_w, h=card_h))
        reqs.extend(_text_box(sid, icon,
            x=x + 150000, y=2150000, w=500000, h=350000, font_size=22, color=C_RED_SOFT))
        reqs.extend(_text_box(sid, tag,
            x=x + 150000, y=2480000, w=card_w - 300000, h=200000,
            font_size=8, color=C_RED_SOFT, bold=True))
        reqs.extend(_text_box(sid, title,
            x=x + 150000, y=2660000, w=card_w - 300000, h=250000,
            font_size=13, color=C_WHITE, bold=True))
        reqs.extend(_text_box(sid, body,
            x=x + 150000, y=2890000, w=card_w - 300000, h=700000,
            font_size=10, color=C_GRAY))

    reqs.extend(_slide_footer(sid, "01 / 08"))
    return reqs


def _build_s02(sid: str) -> list[dict]:
    """Slide 02 — 制裁金の発動（AMATの事例）"""
    reqs = []
    reqs.extend(_slide_header(sid,
        label="CASE STUDY — 制裁実例",
        title="制裁金の発動（AMATの事例）",
        headline="Applied Materials、半導体製造装置の対中輸出で DOJ 調査——$153M 超の制裁合意は「他人事」ではない",
        label_color=C_RED_SOFT))

    # Left column: case summary card
    reqs.extend(_card(sid, x=457200, y=1320000, w=3800000, h=1400000))
    reqs.extend(_text_box(sid, "Applied Materials（AMAT）",
        x=600000, y=1380000, w=3500000, h=300000,
        font_size=16, color=C_WHITE, bold=True))
    reqs.extend(_text_box(sid,
        "米国最大の半導体製造装置メーカー。DOJ（米国司法省）が EAR（輸出管理規則）\n"
        "違反の疑いで調査。中国半導体メーカー SMIC（エンティティリスト掲載）への\n"
        "装置出荷が問題の焦点となった。",
        x=600000, y=1680000, w=3500000, h=500000,
        font_size=10, color=C_GRAY))

    # Stat: $153M
    reqs.extend(_card(sid, x=457200, y=2800000, w=1800000, h=600000))
    reqs.extend(_text_box(sid, "$153M+",
        x=500000, y=2850000, w=1700000, h=350000,
        font_size=26, color=C_RED_SOFT, bold=True, align="CENTER"))
    reqs.extend(_text_box(sid, "報道ベースの制裁合意報告額",
        x=500000, y=3180000, w=1700000, h=180000,
        font_size=9, color=C_GRAY, align="CENTER"))

    # Stat: 刑事
    reqs.extend(_card(sid, x=2350000, y=2800000, w=1900000, h=600000))
    reqs.extend(_text_box(sid, "刑事罰 + 行政処分",
        x=2400000, y=2880000, w=1800000, h=300000,
        font_size=14, color=C_RED_SOFT, bold=True, align="CENTER"))
    reqs.extend(_text_box(sid, "個人への懲役・業務停止命令",
        x=2400000, y=3150000, w=1800000, h=200000,
        font_size=9, color=C_GRAY, align="CENTER"))

    # Right column: timeline + impact
    # Timeline header
    reqs.extend(_text_box(sid, "事件の経緯",
        x=4500000, y=1300000, w=4000000, h=200000,
        font_size=9, color=C_GRAY, bold=True))

    tl_items = [
        ("2020–21", "SMIC が米商務省エンティティリストに掲載。\nAMAT は第三国経由の出荷を継続との疑惑。"),
        ("2022–23", "DOJ・Commerce Dept による調査開始。\n株主訴訟も並行し、株価への影響と経営陣対応が注目。"),
        ("2024",    "$153M 超の民事和解合意が報道。\n輸出コンプライアンス体制の全面見直しを実施。"),
    ]
    tl_x = 4500000
    for i, (year, text) in enumerate(tl_items):
        ty = 1550000 + i * 600000
        # dot
        reqs.extend(_card(sid, x=tl_x, y=ty + 60000, w=90000, h=90000,
                         color=C_BLUE))
        # year
        reqs.extend(_text_box(sid, year,
            x=tl_x + 180000, y=ty, w=600000, h=200000,
            font_size=9, color=C_BLUE, bold=True))
        # text
        reqs.extend(_text_box(sid, text,
            x=tl_x + 180000, y=ty + 200000, w=4000000, h=320000,
            font_size=10, color=C_GRAY))

    # Impact cards (3 small)
    impacts = [
        ("⚖", "刑事罰", "個人への懲役刑・法人への重大罰金。代表者の監督責任も問われる。"),
        ("🚫", "行政処分", "輸出業務停止命令。グローバルサプライチェーンの即時停止リスク。"),
        ("📉", "信頼失墜", "株価下落・顧客離反・資金調達コスト上昇。"),
    ]
    imp_w = 1250000
    for i, (icon, title, body) in enumerate(impacts):
        ix = 4500000 + i * (imp_w + 100000)
        reqs.extend(_card(sid, x=ix, y=3500000, w=imp_w, h=1100000))
        reqs.extend(_text_box(sid, icon,
            x=ix + 100000, y=3600000, w=300000, h=300000, font_size=18, color=C_RED_SOFT))
        reqs.extend(_text_box(sid, title,
            x=ix + 100000, y=3880000, w=imp_w - 200000, h=220000,
            font_size=11, color=C_WHITE, bold=True))
        reqs.extend(_text_box(sid, body,
            x=ix + 100000, y=4080000, w=imp_w - 200000, h=400000,
            font_size=9, color=C_GRAY))

    reqs.extend(_slide_footer(sid, "02 / 08"))
    return reqs


def _build_s03(sid: str) -> list[dict]:
    """Slide 03 — AI活用の難易度"""
    reqs = []
    reqs.extend(_slide_header(sid,
        label="CONSTRAINT — AI 活用の壁",
        title="安全保障貿易管理における AI 活用の難易度",
        headline="「汎用 LLM に任せる」では解決できない——コンプライアンス AI 固有の 3 つの壁と、NeuroSymbolic AI による突破口",
        label_color=C_GOLD))

    # Row 1: 3 walls (fail cards)
    row1 = [
        ("🤔", "壁 1", "ハルシネーション",
         "存在しない条文番号を引用。数値閾値の判定で一貫性を欠く。\nコンプライアンス文脈での「もっともらしい誤り」は最悪のパターン。"),
        ("📅", "壁 2", "知識カットオフ問題",
         "外為法・輸出令は頻繁に改正される。トレーニング後の改正内容\nを LLM は知らない。「古い法律ベースで OK」は法的リスクそのもの。"),
        ("⬛", "壁 3", "判断根拠の不透明性",
         "「規制対象ではありません」と言われても根拠が示せない。\n担当者がダブルチェックできず、監査証跡にもならない。"),
    ]
    card_w, card_h, gap = 2700000, 1200000, 200000
    for i, (icon, tag, title, body) in enumerate(row1):
        x = 457200 + i * (card_w + gap)
        reqs.extend(_card(sid, x=x, y=1320000, w=card_w, h=card_h))
        # Red left border indicator
        reqs.extend(_accent_bar(sid, x=x, y=1320000, h=card_h, w=57150, color=C_RED_SOFT))
        reqs.extend(_text_box(sid, f"{icon}  {tag}",
            x=x + 150000, y=1380000, w=card_w - 300000, h=220000,
            font_size=10, color=C_RED_SOFT, bold=True))
        reqs.extend(_text_box(sid, title,
            x=x + 150000, y=1570000, w=card_w - 300000, h=250000,
            font_size=14, color=C_WHITE, bold=True))
        reqs.extend(_text_box(sid, body,
            x=x + 150000, y=1800000, w=card_w - 300000, h=650000,
            font_size=10, color=C_GRAY))

    # Arrow divider row (text)
    reqs.extend(_text_box(sid, "↓  NeuroSymbolic AI による解法",
        x=457200, y=2620000, w=8229600, h=220000,
        font_size=11, color=C_BLUE, bold=True, align="CENTER"))

    # Row 2: 3 solutions (good cards)
    row2 = [
        ("✦", "解法 1", "規制知識 = オントロジー",
         "LLM に規制を「覚えさせない」。正確性はオントロジー JSON が保証。\n法改正 = シードファイル更新のみ。エンジン改修は不要。"),
        ("✦", "解法 2", "Neural × Symbolic 分業",
         "FAISS/LLM は候補抽出・対話生成を担当。\n閾値判定・条文適用は Symbolic ルールエンジンが担当。\n確率的判断と決定論的判断を明確に分離する。"),
        ("✦", "解法 3", "三層の根拠付き出力",
         "条文・スコア・一致語・判定ロジックをすべて記録。\nPDF レポート + DB 監査ログで説明責任を担保。\n監査・立入検査にも対応した証跡設計。"),
    ]
    for i, (icon, tag, title, body) in enumerate(row2):
        x = 457200 + i * (card_w + gap)
        reqs.extend(_card(sid, x=x, y=2900000, w=card_w, h=1300000, color=C_BG2))
        reqs.extend(_accent_bar(sid, x=x, y=2900000, h=1300000, w=57150, color=C_BLUE))
        reqs.extend(_text_box(sid, f"{icon}  {tag}",
            x=x + 150000, y=2960000, w=card_w - 300000, h=220000,
            font_size=10, color=C_BLUE_DIM, bold=True))
        reqs.extend(_text_box(sid, title,
            x=x + 150000, y=3160000, w=card_w - 300000, h=250000,
            font_size=14, color=C_WHITE, bold=True))
        reqs.extend(_text_box(sid, body,
            x=x + 150000, y=3390000, w=card_w - 300000, h=700000,
            font_size=10, color=C_GRAY))

    reqs.extend(_slide_footer(sid, "03 / 08"))
    return reqs


def _build_s04(sid: str) -> list[dict]:
    """Slide 04 — AI Trade Compliance Management（プロダクト画面）"""
    reqs = []
    reqs.extend(_slide_header(sid,
        label="PRODUCT — プロダクト紹介",
        title="AI Trade Compliance Management",
        headline="あらゆる企業に、エキスパートレベルの安全保障輸出管理判断を——NeuroSymbolic AI 統合プラットフォーム",
        label_color=C_BLUE))

    # ── ブラウザウィンドウ風モックアップ ────────────────────────────
    # Browser frame bg
    reqs.extend(_card(sid, x=457200, y=1320000, w=8229600, h=3650000, color=C_DARK2))
    # Browser chrome bar
    reqs.extend(_card(sid, x=457200, y=1320000, w=8229600, h=300000,
                     color={"red": 0.067, "green": 0.094, "blue": 0.153}))

    # Traffic light dots (simulated as small colored rectangles)
    dot_colors = [
        {"red": 1.0, "green": 0.373, "blue": 0.341},   # red #ff5f57
        {"red": 1.0, "green": 0.741, "blue": 0.180},   # yellow #ffbd2e
        {"red": 0.157, "green": 0.784, "blue": 0.251}, # green #28c840
    ]
    for i, dc in enumerate(dot_colors):
        dx = 600000 + i * 200000
        reqs.extend(_card(sid, x=dx, y=1390000, w=100000, h=100000, color=dc))

    # URL bar
    reqs.extend(_card(sid, x=1300000, y=1355000, w=4500000, h=220000, color=C_BG2))
    reqs.extend(_text_box(sid, "localhost:8001/ui/transactions/5/detail",
        x=1350000, y=1370000, w=4400000, h=190000,
        font_size=9, color=C_GRAY_DIM, font_family="Courier New"))

    # ── Product UI content ───────────────────────────────────────
    # Transaction header
    reqs.extend(_text_box(sid, "取引 TX-2026-0083 — 工業用マスキングテープ（耐熱・高精度）",
        x=600000, y=1680000, w=5500000, h=220000,
        font_size=11, color=C_WHITE, bold=True))

    # Badges
    for j, (label, col) in enumerate([
        ("FAISS判定: LOW", C_AMBER),
        ("🛡 キャッチオール推奨", C_PURPLE),
    ]):
        bx = 6200000 + j * 1100000
        reqs.extend(_card(sid, x=bx, y=1680000, w=1000000, h=220000, color=C_BG2))
        reqs.extend(_text_box(sid, label,
            x=bx + 50000, y=1710000, w=900000, h=180000,
            font_size=8, color=col, bold=True, align="CENTER"))

    # Two-list result cards (3 col)
    tl_items = [
        ("INTERSECTION（高優先）", "0", "リスト規制両面一致なし", C_GREEN_DIM),
        ("CORE ONLY（中優先）",    "0", "申告用途一致なし",       C_GREEN_DIM),
        ("EXPANDED（転用可能性）", "2", "LOW リスク 2件",         C_AMBER),
    ]
    for i, (label, num, sub, nc) in enumerate(tl_items):
        cx = 600000 + i * 2700000
        reqs.extend(_card(sid, x=cx, y=1960000, w=2600000, h=600000, color=C_BG2))
        reqs.extend(_text_box(sid, label,
            x=cx + 100000, y=2000000, w=2400000, h=180000,
            font_size=8, color=C_GRAY))
        reqs.extend(_text_box(sid, num,
            x=cx + 100000, y=2150000, w=500000, h=280000,
            font_size=24, color=nc, bold=True))
        reqs.extend(_text_box(sid, sub,
            x=cx + 600000, y=2210000, w=1900000, h=180000,
            font_size=9, color=nc))

    # Catchall panel (h=1500000 to accommodate taller text boxes)
    reqs.extend(_card(sid, x=600000, y=2630000, w=8000000, h=1500000,
                     color={"red": 0.065, "green": 0.071, "blue": 0.130}))
    # Purple left border
    reqs.extend(_accent_bar(sid, x=600000, y=2630000, h=1500000, w=57150, color=C_PURPLE))

    reqs.extend(_text_box(sid, "🛡  キャッチオール自己判定 — CAUTION（要注意）",
        x=750000, y=2700000, w=5000000, h=230000,
        font_size=11, color=C_PURPLE, bold=True))
    reqs.extend(_text_box(sid,
        "仕向地: 中華人民共和国（CN） / 懸念国 MEDIUM  |  EAR Groups: D:1  D:3  D:4  D:5\n"
        "Country Chart: NS1 / MT / NP2 / CB3 / RS1  |  Red Flag: 0/7 検出なし",
        x=750000, y=2950000, w=7700000, h=300000,
        font_size=10, color=C_GRAY))
    reqs.extend(_text_box(sid,
        "推奨アクション:\n"
        "• エンドユーザー詳細調査の実施\n"
        "• EUC・最終用途申告書を取得・保管\n"
        "• 状況変化時は安全保障貿易管理部門へ報告",
        x=750000, y=3290000, w=7700000, h=450000,
        font_size=10, color=C_GRAY))
    reqs.extend(_text_box(sid, "判定根拠: 懸念国（別表第3）リスクレベル MEDIUM | EAR Country Chart 5制御列該当",
        x=750000, y=3980000, w=7700000, h=130000,
        font_size=9, color=C_GRAY_DIM))

    reqs.extend(_slide_footer(sid, "04 / 08"))
    return reqs


def _build_s05(sid: str) -> list[dict]:
    """Slide 05 — ソリューション全体概要"""
    reqs = []
    reqs.extend(_slide_header(sid,
        label="SOLUTION OVERVIEW — 全体構成",
        title="ソリューションの全体概要",
        headline="NeuroSymbolic AI × 7 モジュール——リスト規制からキャッチオール規制・特許リスクまで一貫カバレッジ",
        label_color=C_BLUE))

    # Three columns: Neural | Center | Symbolic
    col_w = 2600000
    # Neural zone bg
    r1, r2 = _bg_rect(sid,
        {"red": 0.039, "green": 0.063, "blue": 0.157},  # dark blue tint
        w=col_w, h=3500000, x=457200, y=1320000, obj_id=_uid("neural_zone"))
    reqs.extend([r1, r2])
    reqs.extend(_text_box(sid, "NEURAL LAYER",
        x=457200, y=1340000, w=col_w, h=200000,
        font_size=9, color=C_BLUE_DIM, bold=True, align="CENTER"))

    # Symbolic zone bg
    r1, r2 = _bg_rect(sid,
        {"red": 0.039, "green": 0.110, "blue": 0.063},  # dark green tint
        w=col_w, h=3500000, x=457200 + col_w * 2, y=1320000, obj_id=_uid("sym_zone"))
    reqs.extend([r1, r2])
    reqs.extend(_text_box(sid, "SYMBOLIC LAYER",
        x=457200 + col_w * 2, y=1340000, w=col_w, h=200000,
        font_size=9, color=C_GREEN_DIM, bold=True, align="CENTER"))

    # Neural items
    neural_items = [
        ("FAISS Layer A", "申告用途 ↔ 規制要件"),
        ("FAISS Layer B", "転用可能性マッチング"),
        ("FAISS Layer C", "HS コード照合"),
        ("Claude API",    "対話生成・質問補完"),
    ]
    for i, (t, s) in enumerate(neural_items):
        iy = 1580000 + i * 600000
        reqs.extend(_card(sid, x=570000, y=iy, w=col_w - 200000, h=500000, color=C_BG2))
        reqs.extend(_text_box(sid, t,
            x=670000, y=iy + 50000, w=col_w - 400000, h=200000,
            font_size=11, color=C_BLUE_DIM, bold=True))
        reqs.extend(_text_box(sid, s,
            x=670000, y=iy + 250000, w=col_w - 400000, h=180000,
            font_size=9, color=C_GRAY))

    # Center: HanteiAgent
    cx = 457200 + col_w
    # Border card
    r1, r2 = _bg_rect(sid, C_BG, w=col_w, h=2000000, x=cx, y=1550000, obj_id=_uid("agent_bg"))
    reqs.extend([r1, r2])
    r1, r2 = _bg_rect(sid, C_BLUE, w=col_w, h=38100, x=cx, y=1550000, obj_id=_uid("agent_border_t"))
    reqs.extend([r1, r2])
    r1, r2 = _bg_rect(sid, C_BLUE, w=col_w, h=38100, x=cx, y=3512000, obj_id=_uid("agent_border_b"))
    reqs.extend([r1, r2])
    reqs.extend(_text_box(sid, "HanteiAgent",
        x=cx + 100000, y=1650000, w=col_w - 200000, h=300000,
        font_size=20, color=C_BLUE_DIM, bold=True, align="CENTER"))
    reqs.extend(_text_box(sid, "対話型 NeuroSymbolic エージェント",
        x=cx + 100000, y=1930000, w=col_w - 200000, h=200000,
        font_size=10, color=C_GRAY, align="CENTER"))
    reqs.extend(_card(sid, x=cx + 200000, y=2200000, w=col_w - 400000, h=200000, color=C_DARK2))
    reqs.extend(_text_box(sid, "required − known = missing",
        x=cx + 200000, y=2210000, w=col_w - 400000, h=180000,
        font_size=9, color=C_CYAN, align="CENTER", font_family="Courier New"))
    reqs.extend(_text_box(sid, "セッション DB 永続化\nTransactionContext 状態管理",
        x=cx + 100000, y=2480000, w=col_w - 200000, h=300000,
        font_size=9, color=C_GRAY_DIM, align="CENTER"))

    # Catchall engine (below HanteiAgent)
    reqs.extend(_card(sid, x=cx, y=3600000, w=col_w, h=500000,
                     color={"red": 0.065, "green": 0.059, "blue": 0.125}))
    reqs.extend(_accent_bar(sid, x=cx, y=3600000, h=500000, w=57150, color=C_PURPLE))
    reqs.extend(_text_box(sid, "Catchall Symbolic Engine",
        x=cx + 120000, y=3650000, w=col_w - 240000, h=200000,
        font_size=11, color=C_PURPLE, bold=True))
    reqs.extend(_text_box(sid, "輸出令第4条・第4条の2 / E:1 禁輸国 / EAR Country Chart / Red Flag 7項目",
        x=cx + 120000, y=3840000, w=col_w - 240000, h=200000,
        font_size=8, color=C_GRAY))

    # Output arrow & button
    reqs.extend(_card(sid, x=cx + 200000, y=4200000, w=col_w - 400000, h=400000, color=C_BLUE))
    reqs.extend(_text_box(sid, "判定レポート出力",
        x=cx + 200000, y=4230000, w=col_w - 400000, h=220000,
        font_size=12, color=C_WHITE, bold=True, align="CENTER"))
    reqs.extend(_text_box(sid, "PDF（根拠付き） / CSV / 監査ログ",
        x=cx + 200000, y=4420000, w=col_w - 400000, h=150000,
        font_size=8, color=C_BLUE_DIM, align="CENTER"))

    # Symbolic items
    sym_items = [
        ("オントロジーエンジン", "29項目 規制知識グラフ"),
        ("外為法 リスト規制",  "輸出令別表第3（17項目）"),
        ("ECCN / EAR 規制",   "米国輸出管理規則 12分類"),
        ("EAR Country Chart", "47カ国 13制御列データ"),
    ]
    sx = 457200 + col_w * 2
    for i, (t, s) in enumerate(sym_items):
        iy = 1580000 + i * 600000
        reqs.extend(_card(sid, x=sx + 100000, y=iy, w=col_w - 200000, h=500000, color=C_BG2))
        reqs.extend(_text_box(sid, t,
            x=sx + 200000, y=iy + 50000, w=col_w - 400000, h=200000,
            font_size=11, color=C_GREEN_DIM, bold=True))
        reqs.extend(_text_box(sid, s,
            x=sx + 200000, y=iy + 250000, w=col_w - 400000, h=180000,
            font_size=9, color=C_GRAY))

    reqs.extend(_slide_footer(sid, "05 / 08"))
    return reqs


def _build_s06(sid: str) -> list[dict]:
    """Slide 06 — 技術的優位性"""
    reqs = []
    reqs.extend(_slide_header(sid,
        label="TECHNOLOGY — 技術優位性",
        title="ソリューションの技術的優位性",
        headline="LLM に規制知識を「覚えさせない」設計——NeuroSymbolic が実現するハルシネーション不在・法改正即時対応・完全監査証跡",
        label_color=C_BLUE))

    # Left: VS table
    reqs.extend(_text_box(sid, "従来アプローチとの比較",
        x=457200, y=1340000, w=4000000, h=200000,
        font_size=9, color=C_GRAY, bold=True))

    # Table header
    headers = ["評価軸", "汎用 LLM（従来）", "本システム（NeuroSymbolic）"]
    header_w = [1500000, 1500000, 1800000]
    hx = 457200
    for i, (hdr, hw) in enumerate(zip(headers, header_w)):
        hcol = C_RED_SOFT if i == 1 else (C_GREEN_DIM if i == 2 else C_GRAY)
        reqs.extend(_card(sid, x=hx, y=1580000, w=hw, h=230000, color=C_DARK2))
        reqs.extend(_text_box(sid, hdr,
            x=hx + 50000, y=1600000, w=hw - 100000, h=190000,
            font_size=9, color=hcol, bold=True))
        hx += hw

    vs_rows = [
        ("規制知識の正確性",  "ハルシネーション頻発",       "オントロジー 100% 保証"),
        ("法改正への対応",    "カットオフ依存・追従不可",    "シード更新のみで即時対応"),
        ("判断根拠の透明性",  "ブラックボックス",            "条文・スコア・閾値を明示"),
        ("監査証跡",          "なし",                        "全判定 DB 記録・PDF 保存"),
        ("キャッチオール",    "判断基準なし",                "Symbolic 6ステップ自動判定"),
    ]
    row_h = 360000
    for ri, (axis, bad, good) in enumerate(vs_rows):
        ry = 1810000 + ri * row_h
        bg_col = C_BG if ri % 2 == 0 else C_BG2
        # Axis cell
        reqs.extend(_card(sid, x=457200, y=ry, w=1500000, h=row_h, color=bg_col))
        reqs.extend(_text_box(sid, axis,
            x=507200, y=ry + 80000, w=1400000, h=200000,
            font_size=10, color=C_WHITE))
        # Bad cell
        reqs.extend(_card(sid, x=1957200, y=ry, w=1500000, h=row_h, color=bg_col))
        reqs.extend(_text_box(sid, bad,
            x=2007200, y=ry + 80000, w=1400000, h=200000,
            font_size=10, color=C_RED_DIM))
        # Good cell
        reqs.extend(_card(sid, x=3457200, y=ry, w=1800000, h=row_h, color=bg_col))
        reqs.extend(_text_box(sid, good,
            x=3507200, y=ry + 80000, w=1700000, h=200000,
            font_size=10, color=C_GREEN_DIM, bold=True))

    # Right: 4 tech pillars
    pillars = [
        ("🔬", "三層 FAISS インデックス",
         "Layer A（申告用途）・B（転用可能性）・C（HS コード）。\ne5-large 多言語埋め込みで日英混在テキストに対応。"),
        ("🛡", "Symbolic キャッチオールエンジン",
         "E:1 禁輸国即時判定・EAR Country Chart 13列証拠付与\n・Red Flag 7項目。47カ国データベース常時参照。"),
        ("📖", "知識とコードの完全分離",
         "規制知識は JSON オントロジーとして独立管理。\n法改正時はシード更新のみ——エンジン改修不要。"),
        ("🗄", "WAL 監査ログ + 完全証跡",
         "SQLite WAL + PostgreSQL 横断ログ。\n全判定セッション・根拠・タイムスタンプを永続化。"),
    ]
    px, py, pw, ph = 5550000, 1340000, 3050000, 880000
    for i, (icon, title, body) in enumerate(pillars):
        iy = py + i * (ph + 100000)
        reqs.extend(_card(sid, x=px, y=iy, w=pw, h=ph, color=C_BG2))
        reqs.extend(_accent_bar(sid, x=px, y=iy, h=ph, w=57150, color=C_BLUE))
        reqs.extend(_text_box(sid, f"{icon}  {title}",
            x=px + 150000, y=iy + 80000, w=pw - 300000, h=250000,
            font_size=12, color=C_WHITE, bold=True))
        reqs.extend(_text_box(sid, body,
            x=px + 150000, y=iy + 320000, w=pw - 300000, h=500000,
            font_size=10, color=C_GRAY))

    reqs.extend(_slide_footer(sid, "06 / 08"))
    return reqs


def _build_s07(sid: str) -> list[dict]:
    """Slide 07 — レポーティング・ダッシュボーディング"""
    reqs = []
    reqs.extend(_slide_header(sid,
        label="OUTPUT — 可視化・証跡",
        title="レポーティング、ダッシュボーディング",
        headline="判定根拠・監査証跡・経営指標——すべてが自動で記録・可視化。PDF 1本でリスト規制判定＋キャッチオール自己審査を完結",
        label_color=C_BLUE))

    # ── Left: Dashboard mockup ─────────────────────────────────
    reqs.extend(_text_box(sid, "📊  管理ダッシュボード",
        x=457200, y=1340000, w=4000000, h=200000,
        font_size=9, color=C_GRAY, bold=True))

    # Dashboard browser frame
    reqs.extend(_card(sid, x=457200, y=1580000, w=4200000, h=3150000, color=C_DARK2))
    # Browser bar
    reqs.extend(_card(sid, x=457200, y=1580000, w=4200000, h=250000, color=C_BG2))
    reqs.extend(_text_box(sid, "localhost:8001/ui/dashboard",
        x=800000, y=1610000, w=2800000, h=190000,
        font_size=8, color=C_GRAY_DIM, font_family="Courier New"))

    # Stat cards (2x2)
    db_stats = [
        ("12",  "総取引件数", C_BLUE_DIM),
        ("2",   "HIGH リスク", C_RED_SOFT),
        ("3",   "CA推奨",     C_PURPLE),
        ("9",   "判定完了",   C_GREEN_DIM),
    ]
    for i, (num, label, nc) in enumerate(db_stats):
        gx = 570000 + (i % 2) * 2000000
        gy = 1890000 + (i // 2) * 500000
        reqs.extend(_card(sid, x=gx, y=gy, w=1900000, h=430000, color=C_BG2))
        reqs.extend(_text_box(sid, num,
            x=gx + 100000, y=gy + 50000, w=600000, h=280000,
            font_size=22, color=nc, bold=True))
        reqs.extend(_text_box(sid, label,
            x=gx + 700000, y=gy + 130000, w=1100000, h=200000,
            font_size=10, color=C_GRAY))

    # Transaction table (mini)
    tx_cols = ["取引 ID", "品目", "リスク", "CA"]
    col_xs  = [570000, 1250000, 3350000, 3900000]
    col_ws  = [650000, 2050000, 500000, 600000]

    # Table header
    for j, (ch, cx, cw) in enumerate(zip(tx_cols, col_xs, col_ws)):
        reqs.extend(_card(sid, x=cx, y=2940000, w=cw, h=200000, color=C_BG2))
        reqs.extend(_text_box(sid, ch,
            x=cx + 50000, y=2960000, w=cw - 100000, h=160000,
            font_size=8, color=C_GRAY, bold=True))

    tx_rows = [
        ("TX-2026-0083", "工業用マスキングテープ（耐熱）", "LOW",    "CA推奨", C_GREEN_DIM, C_PURPLE),
        ("TX-2026-0081", "精密センサー（半導体向け）",    "MEDIUM",  "—",     C_AMBER,     C_GRAY),
        ("TX-2026-0078", "半導体製造装置部品",           "HIGH",    "完了",   C_RED_SOFT,  C_GREEN_DIM),
    ]
    for ri, (tid, item, risk, ca, rc, cc) in enumerate(tx_rows):
        ry = 3180000 + ri * 360000
        bg = C_BG2 if ri % 2 else C_BG
        for cx_val, cw_val in zip(col_xs, col_ws):
            reqs.extend(_card(sid, x=cx_val, y=ry, w=cw_val, h=320000, color=bg))

        row_data = [(tid, C_GRAY), (item, C_WHITE), (risk, rc), (ca, cc)]
        for j, ((txt, tc), cx_val, cw_val) in enumerate(zip(row_data, col_xs, col_ws)):
            reqs.extend(_text_box(sid, txt,
                x=cx_val + 50000, y=ry + 70000, w=cw_val - 100000, h=200000,
                font_size=9, color=tc))

    # ── Right: PDF report mockup ──────────────────────────────
    reqs.extend(_text_box(sid, "📄  PDF 判定レポート（自動生成）",
        x=5000000, y=1340000, w=3500000, h=200000,
        font_size=9, color=C_GRAY, bold=True))

    # PDF frame (white bg)
    r1, r2 = _bg_rect(sid, {"red": 1.0, "green": 1.0, "blue": 1.0},
                      w=3700000, h=3150000, x=5000000, y=1580000, obj_id=_uid("pdf_bg"))
    reqs.extend([r1, r2])

    # PDF header
    r1, r2 = _bg_rect(sid, {"red": 0.118, "green": 0.251, "blue": 0.686},
                      w=3700000, h=200000, x=5000000, y=1580000, obj_id=_uid("pdf_hdr"))
    reqs.extend([r1, r2])
    reqs.extend(_text_box(sid, "AI 該非判定レポート — TX-2026-0083",
        x=5050000, y=1610000, w=3600000, h=160000,
        font_size=10, color={"red": 1.0, "green": 1.0, "blue": 1.0}, bold=True))

    # Section 3 (Two-List)
    r1, r2 = _bg_rect(sid, {"red": 0.118, "green": 0.251, "blue": 0.686},
                      w=1700000, h=160000, x=5050000, y=1840000, obj_id=_uid("s3_bar"))
    reqs.extend([r1, r2])
    reqs.extend(_text_box(sid, "SECTION 3 — TWO-LIST 判定",
        x=5100000, y=1855000, w=1600000, h=130000,
        font_size=8, color={"red": 1.0, "green": 1.0, "blue": 1.0}, bold=True))

    s3_content = (
        "Intersection: 0  ·  Core Only: 0  ·  Expanded: 2\n"
        "上位候補: 外為令別表 4-7 (score: 0.24)\n"
        "ECCN EAR99 類似品目 (score: 0.18)\n"
        "→ リスト規制候補なし（LOW）"
    )
    reqs.extend(_text_box(sid, s3_content,
        x=5050000, y=2020000, w=3600000, h=500000,
        font_size=9, color={"red": 0.216, "green": 0.216, "blue": 0.216}))

    # Section 4 (Catchall)
    r1, r2 = _bg_rect(sid, {"red": 0.573, "green": 0.251, "blue": 0.063},
                      w=1800000, h=160000, x=5050000, y=2580000, obj_id=_uid("s4_bar"))
    reqs.extend([r1, r2])
    reqs.extend(_text_box(sid, "SECTION 4 — キャッチオール判定",
        x=5100000, y=2595000, w=1700000, h=130000,
        font_size=8, color={"red": 1.0, "green": 1.0, "blue": 1.0}, bold=True))

    reqs.extend(_text_box(sid, "判定: 要注意（CAUTION）",
        x=5050000, y=2780000, w=1800000, h=200000,
        font_size=10, color={"red": 0.573, "green": 0.251, "blue": 0.063}, bold=True))
    reqs.extend(_text_box(sid,
        "仕向地: 中華人民共和国（CN）/ 懸念国 MEDIUM\n"
        "EAR Groups: D:1  D:3  D:4  D:5\n"
        "Country Chart: NS1 / MT / NP2 / CB3 / RS1\n"
        "Red Flag: 0/7 検出なし  ✓",
        x=5050000, y=2980000, w=3600000, h=700000,
        font_size=9, color={"red": 0.216, "green": 0.216, "blue": 0.216}))

    reqs.extend(_text_box(sid, "判定日時: 2026-03-20 09:14 UTC  ·  担当: Admin User",
        x=5050000, y=3760000, w=3600000, h=160000,
        font_size=8, color={"red": 0.608, "green": 0.608, "blue": 0.608}))

    reqs.extend(_slide_footer(sid, "07 / 08"))
    return reqs


def _build_s08(sid: str, video_drive_id: str | None) -> list[dict]:
    """Slide 08 — 各モジュールの詳細"""
    reqs = []
    reqs.extend(_slide_header(sid,
        label="MODULES — 機能詳細",
        title="各モジュールの詳細",
        headline="7 つのマイクロサービスが連携して企業の輸出管理インフラを形成する——独立稼働かつ統合 API で全モジュール連携",
        label_color=C_BLUE))

    modules_row1 = [
        ("🔍", "AI 該非判定", "ai_validation  :8001",
         "FAISS Three-List 判定\nHanteiAgent 対話補完\nキャッチオール Symbolic Engine\nPDF / CSV エクスポート",
         True),
        ("🧩", "品目管理", "ai_classification  :8002",
         "品目マスタ管理\nBOM（部品表）照合\n外部 ERP 連携 API\n品目別輸出管理属性付与",
         True),
        ("🔬", "R&D リスク評価", "rnd_assessment  :8003",
         "研究開発段階のリスク早期検出\nIP レビュー・特許照合連携\n「シフトレフト」型コンプライアンス",
         True),
        ("💬", "DAP コーチング", "dap  :8004",
         "輸出管理担当者向け DAP\nAI コーチがリアルタイムガイド\nChat Widget 組み込み対応",
         False),
    ]
    modules_row2 = [
        ("🔭", "特許検索", "patent_search  :8005",
         "Google Patents × BigQuery\nR&D リスク評価・ECCN 分類補助\n特許技術情報を横断参照",
         False),
        ("🗂", "HS 分類", "hs_classifier  :8006",
         "FAISS Layer C による HS コード自動分類\n輸出令別表第1 × HS コードの意味照合\n規制分類を高速判定",
         False),
        ("🏛", "Platform Core", "platform-core  :8000",
         "認証・テナント管理\n共通ダッシュボード / KPI API\n全モジュールのオーケストレーション基盤",
         False),
    ]

    mod_w, mod_h, mod_gap = 2000000, 1300000, 100000
    highlight_color = {"red": 0.051, "green": 0.071, "blue": 0.160}
    normal_color = C_BG2

    # Row 1 (4 modules)
    row1_x_start = 457200
    for i, (icon, name, port, desc, is_hl) in enumerate(modules_row1):
        mx = row1_x_start + i * (mod_w + mod_gap)
        col = highlight_color if is_hl else normal_color
        reqs.extend(_card(sid, x=mx, y=1360000, w=mod_w, h=mod_h, color=col))
        if is_hl:
            reqs.extend(_accent_bar(sid, x=mx, y=1360000, h=mod_h, w=57150, color=C_BLUE))
        reqs.extend(_text_box(sid, f"{icon}  {name}",
            x=mx + 120000, y=1420000, w=mod_w - 240000, h=250000,
            font_size=12, color=C_BLUE_DIM if is_hl else C_GRAY, bold=True))
        reqs.extend(_text_box(sid, port,
            x=mx + 120000, y=1650000, w=mod_w - 240000, h=160000,
            font_size=8, color=C_GRAY_DIM, font_family="Courier New"))
        reqs.extend(_text_box(sid, desc,
            x=mx + 120000, y=1810000, w=mod_w - 240000, h=750000,
            font_size=9, color=C_GRAY))

    # Row 2 (3 modules + video placeholder)
    row2_x_start = 457200
    for i, (icon, name, port, desc, is_hl) in enumerate(modules_row2):
        mx = row2_x_start + i * (mod_w + mod_gap)
        reqs.extend(_card(sid, x=mx, y=2760000, w=mod_w, h=mod_h, color=normal_color))
        reqs.extend(_text_box(sid, f"{icon}  {name}",
            x=mx + 120000, y=2820000, w=mod_w - 240000, h=250000,
            font_size=12, color=C_GRAY, bold=True))
        reqs.extend(_text_box(sid, port,
            x=mx + 120000, y=3050000, w=mod_w - 240000, h=160000,
            font_size=8, color=C_GRAY_DIM, font_family="Courier New"))
        reqs.extend(_text_box(sid, desc,
            x=mx + 120000, y=3210000, w=mod_w - 240000, h=700000,
            font_size=9, color=C_GRAY))

    # Video placeholder (4th column of row 2)
    vx = row2_x_start + 3 * (mod_w + mod_gap)
    reqs.extend(_card(sid, x=vx, y=2760000, w=mod_w, h=mod_h,
                     color={"red": 0.051, "green": 0.059, "blue": 0.071}))
    # Show placeholder text only when no video is available
    if not video_drive_id:
        reqs.extend(_text_box(sid, "▶",
            x=vx, y=2900000, w=mod_w, h=400000,
            font_size=28, color=C_GRAY_DIM, align="CENTER"))
        reqs.extend(_text_box(sid, "デモ動画\n（全ワークフロー紹介）",
            x=vx + 100000, y=3300000, w=mod_w - 200000, h=350000,
            font_size=10, color=C_GRAY_DIM, align="CENTER"))
        reqs.extend(_text_box(sid, "demo_video.mp4",
            x=vx + 100000, y=3620000, w=mod_w - 200000, h=160000,
            font_size=8, color=C_GRAY_DIM, align="CENTER", font_family="Courier New"))

    # Embed actual video if available
    if video_drive_id:
        reqs.append({
            "createVideo": {
                "objectId": _uid("video"),
                "elementProperties": {
                    "pageObjectId": sid,
                    "size": {"width": {"magnitude": mod_w - 100000, "unit": "EMU"},
                             "height": {"magnitude": mod_h - 100000, "unit": "EMU"}},
                    "transform": {"scaleX": 1, "scaleY": 1,
                                  "translateX": vx + 50000,
                                  "translateY": 2810000, "unit": "EMU"},
                },
                "source": "DRIVE",
                "id": video_drive_id,
            }
        })

    reqs.extend(_slide_footer(sid, "08 / 08"))
    return reqs


# ═══════════════════════════════════════════════════════════════
# メイン: 全スライドをビルドして返す
# ═══════════════════════════════════════════════════════════════

def build_all_requests(slides_service, presentation_id: str,
                       video_drive_id: str | None) -> list[dict]:

    prs = slides_service.presentations().get(presentationId=presentation_id).execute()
    existing_slide_ids = [s["objectId"] for s in prs.get("slides", [])]

    all_requests: list[dict] = []

    slide_ids = [
        "slide_01_challenge",
        "slide_02_amat_case",
        "slide_03_ai_constraint",
        "slide_04_product",
        "slide_05_solution",
        "slide_06_technology",
        "slide_07_reporting",
        "slide_08_modules",
    ]

    # 既存スライドを削除
    for sid in existing_slide_ids:
        all_requests.append({"deleteObject": {"objectId": sid}})

    # 新規スライドを作成
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

    # 各スライドのコンテンツを生成
    builders = {
        "slide_01_challenge":    lambda s: _build_s01(s),
        "slide_02_amat_case":    lambda s: _build_s02(s),
        "slide_03_ai_constraint":lambda s: _build_s03(s),
        "slide_04_product":      lambda s: _build_s04(s),
        "slide_05_solution":     lambda s: _build_s05(s),
        "slide_06_technology":   lambda s: _build_s06(s),
        "slide_07_reporting":    lambda s: _build_s07(s),
        "slide_08_modules":      lambda s: _build_s08(s, video_drive_id),
    }

    for sid in slide_ids:
        print(f"  → building {sid}…")
        all_requests.extend(builders[sid](sid))

    return all_requests


# ── 動画アップロード ─────────────────────────────────────────────────

def upload_video(drive_service) -> str | None:
    if not VIDEO_FILE.exists():
        print(f"[INFO] 動画ファイルが見つかりません: {VIDEO_FILE} — スキップします")
        return None
    print(f"[INFO] 動画をアップロード中: {VIDEO_FILE}")
    media = MediaFileUpload(str(VIDEO_FILE), mimetype="video/mp4", resumable=True)
    file_meta = {"name": "AI_TradeManagement_Demo.mp4"}
    result = drive_service.files().create(body=file_meta, media_body=media, fields="id").execute()
    vid = result.get("id")
    print(f"[INFO] アップロード完了: Drive ID = {vid}")
    # 公開設定
    drive_service.permissions().create(
        fileId=vid,
        body={"role": "reader", "type": "anyone"},
    ).execute()
    return vid


# ── エントリポイント ─────────────────────────────────────────────────

def main() -> None:
    print("[1/4] Google 認証中…")
    creds = get_credentials()
    slides_service = build("slides", "v1", credentials=creds)
    drive_service  = build("drive",  "v3", credentials=creds)

    print("[2/4] Google スライドプレゼンテーションを作成中…")
    presentation = slides_service.presentations().create(body={
        "title": "AI Trade Compliance Management — 2026",
    }).execute()
    presentation_id = presentation["presentationId"]
    print(f"       Presentation ID: {presentation_id}")

    print("[3/4] 動画をアップロード中（存在する場合）…")
    video_drive_id = upload_video(drive_service)

    print("[4/4] スライドコンテンツを生成中…")
    requests = build_all_requests(slides_service, presentation_id, video_drive_id)

    # バッチリクエストを 200 件ずつ送信（API 制限対策）
    BATCH_SIZE = 200
    for i in range(0, len(requests), BATCH_SIZE):
        batch = requests[i: i + BATCH_SIZE]
        slides_service.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": batch},
        ).execute()
        print(f"       バッチ送信: {i+1}〜{min(i+BATCH_SIZE, len(requests))} / {len(requests)}")
        time.sleep(0.3)

    url = f"https://docs.google.com/presentation/d/{presentation_id}/edit"
    print("\n✅ 完了!")
    print(f"   URL: {url}")
    print(f"   (ブラウザで開く場合: open '{url}')")


if __name__ == "__main__":
    main()
