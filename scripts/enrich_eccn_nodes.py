#!/usr/bin/env python3
"""
ECCN ノード requirement_text バッチ補完スクリプト

control_nodes.json の ECCN ノードのうち requirement_text が空のものに対して
Claude API を使って規制要件テキストを生成し、control_nodes.json を上書き更新する。

使い方:
    cd /Users/takehirosato/Desktop/AI_TradeManagement
    python scripts/enrich_eccn_nodes.py [--dry-run] [--limit N]

オプション:
    --dry-run   実際には書き込まずプレビューのみ表示
    --limit N   最大 N 件だけ処理（テスト用）
    --force     description が入っているノードも再生成

必要な環境変数:
    ANTHROPIC_API_KEY
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
NODES_PATH = BASE_DIR / "data" / "unified" / "control_nodes.json"
SUPPLEMENT_PATH = BASE_DIR / "data" / "source" / "eccn" / "eccn_requirement_supplement.json"


def load_nodes() -> tuple[dict, list[dict]]:
    with open(NODES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data, data["nodes"]


def load_supplement() -> dict[str, str]:
    """サプリメントファイルから {eccn_id: requirement_text} を読み込む"""
    if SUPPLEMENT_PATH.exists():
        with open(SUPPLEMENT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("nodes", {})
    return {}


def save_supplement(supp: dict[str, str]) -> None:
    """サプリメントファイルを上書き保存する（control_nodes.json は変更しない）"""
    data = {
        "_comment": (
            "ECCN ノード requirement_text サプリメントファイル。"
            "scripts/enrich_eccn_nodes.py が生成・更新する。"
            "builder.py がビルド時に読み込み ECCN ノードに適用する。"
            "このファイルは git 管理対象で、builder.py による再ビルド時も上書きされない。"
        ),
        "nodes": supp,
    }
    with open(SUPPLEMENT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[SAVED] {SUPPLEMENT_PATH}")


def build_prompt(node: dict) -> str:
    eccn = node.get("id", "")
    label = node.get("label") or node.get("description") or ""
    bis_desc = node.get("bis_description", "") or ""
    bis_controls = ", ".join(node.get("bis_controls", []))
    parent_code = eccn.split("(")[0].strip() if "(" in eccn else ""

    sub_note = ""
    if "(" in eccn:
        sub_para = eccn[len(parent_code):].strip()
        sub_note = f"このノードは ECCN {parent_code} のサブ項目 {sub_para} です。"

    return f"""あなたは BIS（米国商務省産業安全保障局）の輸出管理規制（EAR）の専門家です。

以下の ECCN ノードについて、**日本語で** `requirement_text`（規制要件テキスト）を生成してください。

## ECCN コード
{eccn}

## ラベル / 説明
{label}

## BIS 規制理由コード
{bis_controls or "（不明）"}

## BIS 追加説明
{bis_desc or "（なし）"}

{sub_note}

## 出力形式
以下の JSON のみを返してください（説明文不要）:
{{
  "requirement_text": "（日本語で100〜200文字の規制要件概要）"
}}

requirement_text には以下を含めてください:
- この ECCN が規制する技術・物品の概要
- 主な規制理由（安全保障、CB=化学生物、MT=ミサイル等）
- 該当する場合: 主要な用途・技術パラメータの特徴
- 日本の外為法との関連がある場合は簡潔に言及
"""


def call_claude(prompt: str, api_key: str) -> str | None:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        print(f"  [API ERROR] {e}", file=sys.stderr)
        return None


def extract_requirement_text(response: str) -> str | None:
    """Claude のレスポンスから requirement_text を抽出する"""
    try:
        # JSON ブロックを探す
        start = response.find("{")
        end = response.rfind("}") + 1
        if start >= 0 and end > start:
            obj = json.loads(response[start:end])
            return obj.get("requirement_text", "").strip() or None
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="ECCN ノード requirement_text バッチ補完")
    parser.add_argument("--dry-run", action="store_true", help="書き込まずプレビューのみ")
    parser.add_argument("--limit", type=int, default=0, help="最大処理件数（0=無制限）")
    parser.add_argument("--force", action="store_true", help="既存テキストがあっても再生成")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key and not args.dry_run:
        print("[ERROR] ANTHROPIC_API_KEY が設定されていません", file=sys.stderr)
        sys.exit(1)

    data, nodes = load_nodes()
    supplement = load_supplement()

    # 対象ノードを絞り込み
    # --force がない場合は control_nodes.json にも supplement にもないものだけ対象
    targets = [
        n for n in nodes
        if n.get("regime") == "ear" and n.get("type") == "eccn"
        and (args.force or (not n.get("requirement_text") and not supplement.get(n.get("id", ""))))
    ]

    print(f"[INFO] ECCN ノード総数: {sum(1 for n in nodes if n.get('regime')=='ear')}")
    print(f"[INFO] サプリメント既存: {len(supplement)} 件")
    print(f"[INFO] 処理対象: {len(targets)} 件")
    if args.limit:
        targets = targets[:args.limit]
        print(f"[INFO] --limit {args.limit} 適用 → {len(targets)} 件")

    if not targets:
        print("[INFO] 処理対象なし。終了します。")
        return

    updated = 0
    failed = 0

    for i, node in enumerate(targets, 1):
        eccn = node.get("id", "?")
        desc_preview = (node.get("description") or "（説明なし）")[:50]
        print(f"[{i:3}/{len(targets)}] {eccn:14} {desc_preview}")

        if args.dry_run:
            prompt = build_prompt(node)
            print(f"  → (dry-run) プロンプト {len(prompt)} 文字")
            continue

        prompt = build_prompt(node)
        response = call_claude(prompt, api_key)

        if response is None:
            print(f"  → [SKIP] API エラー")
            failed += 1
            time.sleep(2)
            continue

        req_text = extract_requirement_text(response)
        if not req_text:
            print(f"  → [SKIP] JSON パース失敗: {response[:80]}")
            failed += 1
            time.sleep(1)
            continue

        supplement[eccn] = req_text
        node["requirement_text"] = req_text  # 今回のセッション内でも反映
        print(f"  → OK: {req_text[:60]}...")
        updated += 1

        # レート制限対策（Haiku は高速・低コストだが念のため）
        time.sleep(0.5)

    if not args.dry_run:
        save_supplement(supplement)
        print(f"\n[DONE] 更新: {updated} 件, スキップ: {failed} 件")
        print(f"[NEXT] platform-core で /admin/datasets/build を実行して知識グラフを再ビルドしてください")
    else:
        print(f"\n[DRY-RUN DONE] 対象 {len(targets)} 件 (実際の書き込みなし)")


if __name__ == "__main__":
    main()
