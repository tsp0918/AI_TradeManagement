#!/usr/bin/env python3
"""
make_demo_video.py
index.html のスクロール動画 + 日本語ナレーション を生成するスクリプト。

出力: output/demo_video.mp4

必要ツール:
  - playwright (pip install playwright && playwright install chromium)
  - ffmpeg (brew install ffmpeg)
  - macOS say コマンド
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ── パス設定 ─────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent.parent
INDEX_HTML = BASE_DIR / "index.html"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

SCREENSHOT_PATH = OUTPUT_DIR / "fullpage.png"
VIDEO_RAW       = OUTPUT_DIR / "video_raw.mp4"
AUDIO_MERGED    = OUTPUT_DIR / "narration.aiff"
FINAL_VIDEO     = OUTPUT_DIR / "demo_video.mp4"

# ── 動画設定 ─────────────────────────────────────────────────────────────────
VIEWPORT_W  = 1440
VIEWPORT_H  = 900
FPS         = 30
VOICE       = "Kyoko"          # macOS 日本語音声
VOICE_RATE  = 185              # 読み上げ速度 (wpm)

# ── ナレーションスクリプト ────────────────────────────────────────────────────
# (start_ratio, end_ratio) はページ全体高さに対するスクロール位置の比率
# start_ratio=0.0 がページ最上部、1.0 が最下部
NARRATION: list[dict] = [
    {
        "id": "hero",
        "start_ratio": 0.00,
        "end_ratio":   0.16,
        "text": (
            "このプラットフォームは、輸出コンプライアンスをAIによってインテリジェントに変革します。"
            "輸出規制分類から特許リスク審査まで、"
            "複雑な法規制対応をAIエージェントがリアルタイムに支援する統合プラットフォームです。"
            "5つ以上の統合モジュールで、外為法・EAR・ワッセナーの3つの規制体系をカバーします。"
        ),
    },
    {
        "id": "concept",
        "start_ratio": 0.16,
        "end_ratio":   0.32,
        "text": (
            "なぜ今の輸出管理は限界なのか。"
            "グローバル取引の複雑化と規制強化により、担当者の判断負荷は限界に達しています。"
            "規制データベースの断片化、熟練担当者への属人化、"
            "そしてR&D段階でのリスク検出の遅れ。これらが現場の課題です。"
            "このプラットフォームはすべての規制判断プロセスをひとつに統合し、"
            "AIが文脈を読み、人間の専門家が本来の高度な意思決定に集中できる環境を実現します。"
        ),
    },
    {
        "id": "solution",
        "start_ratio": 0.32,
        "end_ratio":   0.46,
        "text": (
            "ソリューションは3つの柱から成ります。"
            "まず、インテリジェント分類。HSコードとECCNをAIが自動分類し、判定根拠とともに提示します。"
            "次に、リアルタイムスクリーニング。取引先や品目を制裁リストとリアルタイムに照合します。"
            "そして、R&Dリスク審査。特許データと輸出規制マトリクスを照合し、開発初期段階でリスクを検出します。"
            "単なる検索ツールではなく、分類・審査・スクリーニングを一気通貫で処理するAIプラットフォームです。"
        ),
    },
    {
        "id": "technology",
        "start_ratio": 0.46,
        "end_ratio":   0.58,
        "text": (
            "アーキテクチャはClaudeAPIを中核とした4層構造です。"
            "AIエージェント層では、マルチターン会話・文脈推論・判定根拠生成を担当。"
            "アプリケーション層は、FastAPIとPythonによる6モジュールのマイクロサービス構成。"
            "データ層では、FAISSセマンティック検索で高速な特許・規制照合を実現。"
            "そして、外為法・EAR・ワッセナー・J-PlatPatなどの規制データソースを統合知識グラフとして参照します。"
        ),
    },
    {
        "id": "features_1to3",
        "start_ratio": 0.58,
        "end_ratio":   0.75,
        "text": (
            "6つの統合モジュールをご紹介します。"
            "R&Dリスク管理モジュールは、232件の輸出規制マトリクスとFAISSセマンティック照合を行い、"
            "開発初期段階から該非リスクを可視化します。"
            "特許検索モジュールは、J-PlatPatとGoogle Patentsを統合し、"
            "実特許データをAI判定の参照データセットに継続的に蓄積します。"
            "品目管理モジュールでは、HSコード・ECCNを紐付けた品目マスターを管理し、"
            "R&D審査からAI判定依頼まで一貫した運用を実現します。"
        ),
    },
    {
        "id": "features_4to6",
        "start_ratio": 0.75,
        "end_ratio":   0.90,
        "text": (
            "AI分類ツールは、品目名と用途概要を入力するだけで"
            "HSコードとECCNを自動推定し、信頼度スコアと根拠テキストをセットで提供します。"
            "AI取引管理モジュールでは、FAISSベクトル検索による高精度エンティティ照合を行い、"
            "R&Dから品目管理、そして取引審査への審査連鎖を証跡として一画面で可視化します。"
            "そして、デジタルアダプションプラットフォームが各業務画面でAIガイダンスを提供し、"
            "担当者の入力品質と審査精度を底上げします。"
        ),
    },
    {
        "id": "cta",
        "start_ratio": 0.90,
        "end_ratio":   1.00,
        "text": (
            "輸出コンプライアンスの未来を、今すぐ体験してください。"
            "デモ環境へのアクセス、技術仕様の詳細、導入相談はお問い合わせフォームから。"
            "複雑な規制対応を、AIの力でシンプルに。"
        ),
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1: フルページスクリーンショット
# ─────────────────────────────────────────────────────────────────────────────

def take_fullpage_screenshot() -> int:
    """Playwright でフルページをキャプチャ。ページの総高さ(px)を返す。"""
    print("[1/4] フルページスクリーンショット取得中...")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": VIEWPORT_W, "height": VIEWPORT_H})
        page.goto(INDEX_HTML.as_uri(), wait_until="networkidle")
        # アニメーション要素を一括表示させる
        page.add_style_tag(content="""
            [data-animate] { opacity: 1 !important; transform: none !important; }
            * { animation-duration: 0s !important; transition-duration: 0s !important; }
        """)
        page.wait_for_timeout(2000)
        page.screenshot(path=str(SCREENSHOT_PATH), full_page=True)
        page_height = page.evaluate("document.documentElement.scrollHeight")
        browser.close()
    print(f"   スクリーンショット保存: {SCREENSHOT_PATH}  (高さ={page_height}px)")
    return page_height


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2: ナレーション音声生成
# ─────────────────────────────────────────────────────────────────────────────

def generate_audio(tmp_dir: Path) -> list[tuple[Path, float]]:
    """各セクションのナレーションを say で生成。(path, duration) リストを返す。"""
    print("[2/4] ナレーション音声を生成中...")
    segments: list[tuple[Path, float]] = []
    for seg in NARRATION:
        aiff_path = tmp_dir / f"{seg['id']}.aiff"
        cmd = [
            "say",
            "-v", VOICE,
            "-r", str(VOICE_RATE),
            "-o", str(aiff_path),
            seg["text"],
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        # 長さ取得
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration", "-of", "csv=p=0", str(aiff_path)],
            capture_output=True, text=True,
        )
        duration = float(result.stdout.strip() or "5.0")
        segments.append((aiff_path, duration))
        print(f"   {seg['id']}: {duration:.1f}s")
    return segments


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3: スクロール動画生成
# ─────────────────────────────────────────────────────────────────────────────

def build_scroll_video(page_height: int, segments: list[tuple[Path, float]]) -> float:
    """ffmpeg の crop フィルターで縦スクロール動画を生成。総尺(秒)を返す。"""
    print("[3/4] スクロール動画を生成中...")

    total_duration = sum(dur for _, dur in segments)
    # 最後の 0.5 秒はページ末尾で停止
    scroll_duration = total_duration - 0.5

    # スクロール可能距離
    max_scroll = max(0, page_height - VIEWPORT_H)

    # ffmpeg vf: crop=W:H:0:y_expr
    # y は時間 t に応じて 0 → max_scroll へ線形移動
    # 各セクションの開始 y 位置はナレーション時間に基づく

    # セクション区切りを時系列で計算
    timestamps: list[tuple[float, float]] = []  # (t_start, y_position)
    t = 0.0
    for i, (seg_meta, (_, dur)) in enumerate(zip(NARRATION, segments)):
        y_top = int(seg_meta["start_ratio"] * max_scroll)
        timestamps.append((t, y_top))
        t += dur
    # 末尾
    timestamps.append((t, max_scroll))

    # keyframe ベースの y(t) を ffmpeg expressions に変換
    # 例: if(lt(t,3), lerp(0,100,t/3), if(lt(t,8), lerp(100,300,(t-3)/5), 300))
    def lerp_expr(t0: float, t1: float, y0: int, y1: int) -> str:
        if abs(t1 - t0) < 0.01:
            return str(y0)
        return f"({y0}+({y1}-{y0})*(t-{t0:.3f})/{(t1-t0):.3f})"

    # 区間を if(lt(t,...), ..., ...) でネスト
    def build_expr(idx: int) -> str:
        if idx >= len(timestamps) - 1:
            return str(timestamps[-1][1])
        t0, y0 = timestamps[idx]
        t1, y1 = timestamps[idx + 1]
        seg_expr = lerp_expr(t0, t1, y0, y1)
        rest_expr = build_expr(idx + 1)
        return f"if(lt(t,{t1:.3f}),{seg_expr},{rest_expr})"

    y_expr = f"clip({build_expr(0)},0,{max_scroll})"

    vf = (
        f"crop={VIEWPORT_W}:{VIEWPORT_H}:0:'{y_expr}',"
        f"format=yuv420p"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-framerate", str(FPS),
        "-i", str(SCREENSHOT_PATH),
        "-vf", vf,
        "-t", str(total_duration),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        str(VIDEO_RAW),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    print(f"   映像生成完了: {VIDEO_RAW}  ({total_duration:.1f}s)")
    return total_duration


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4: 音声を結合 → 映像と合成
# ─────────────────────────────────────────────────────────────────────────────

def merge_audio(segments: list[tuple[Path, float]], tmp_dir: Path) -> None:
    """全セクション音声を結合して映像とミックス。"""
    print("[4/4] 音声合成・最終出力中...")

    # concat フィルターで音声結合
    inputs: list[str] = []
    for path, _ in segments:
        inputs += ["-i", str(path)]

    concat_filter = f"{''.join(f'[{i}:a]' for i in range(len(segments)))}concat=n={len(segments)}:v=0:a=1[aout]"

    cmd_audio = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", concat_filter,
        "-map", "[aout]",
        "-acodec", "pcm_s16le",
        str(AUDIO_MERGED),
    ]
    subprocess.run(cmd_audio, check=True, capture_output=True)

    # 映像 + 音声を MP4 に合成
    cmd_final = [
        "ffmpeg", "-y",
        "-i", str(VIDEO_RAW),
        "-i", str(AUDIO_MERGED),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(FINAL_VIDEO),
    ]
    subprocess.run(cmd_final, check=True, capture_output=True)
    print(f"\n✅ 動画完成: {FINAL_VIDEO}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("  AI Trade Compliance — デモ動画生成スクリプト")
    print("=" * 60)

    with tempfile.TemporaryDirectory(prefix="demo_video_") as tmp:
        tmp_dir = Path(tmp)

        page_height = take_fullpage_screenshot()
        segments    = generate_audio(tmp_dir)
        _total_dur  = build_scroll_video(page_height, segments)
        merge_audio(segments, tmp_dir)

    print(f"\n出力ファイル: {FINAL_VIDEO}")
    print("Finderで開く場合: open output/demo_video.mp4")


if __name__ == "__main__":
    main()
