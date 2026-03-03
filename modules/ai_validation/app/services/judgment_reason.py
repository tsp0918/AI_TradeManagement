"""判定根拠の自然文生成。

two_list.py が compute_two_lists() で組み上げた item dict から
「誰でも読める判定理由テキスト」を生成する。

特許への言及を避けつつ、外為法条文・ECCN・判定類型を
平易な日本語でまとめる。
"""

from __future__ import annotations

from typing import Any


# ── 判定類型の日本語テンプレート ──────────────────────────────────
_REASON_TEMPLATES = {
    "intersection": (
        "{category}に規定される品目として、"
        "申告用途・応用用途の両面から規制該当の可能性が確認されました。"
        "優先的な精査が推奨されます。"
    ),
    "core_only": (
        "申告された用途テキストが{category}の技術要件に直接一致しました。"
        "内容を確認のうえ、輸出許可の要否を判断してください。"
    ),
    "expanded_only": (
        "本品目の応用展開用途から{category}への規制可能性が示唆されています。"
        "実際の最終用途が確定してから改めて精査することを推奨します。"
    ),
}

_CATEGORY_NONE = "規制品目"


def make_judgment_reason(item: dict[str, Any], list_type: str) -> str:
    """item dict と list_type（intersection / core_only / expanded_only）から
    判定根拠テキストを生成して返す。

    Args:
        item:      compute_two_lists() の各 item（enrich 済み）
        list_type: "intersection" | "core_only" | "expanded_only"

    Returns:
        日本語の判定根拠テキスト
    """
    # カテゴリ表示文字列を組み立て
    item_ids   = item.get("item_ids") or []
    cat_label  = item.get("fefta_category_label") or ""
    list_name  = item.get("list_name") or ""
    eccn_refs  = item.get("eccn_refs") or []

    # "外為法 七の項（集積回路等）/ EL-7-19" 形式
    category_parts: list[str] = []
    if cat_label:
        category_parts.append(f"外為法 {cat_label}の項")
    if item_ids:
        category_parts.append(f"（{' / '.join(item_ids[:2])}）")
    elif list_name:
        category_parts.append(f"（{list_name}）")

    category_str = "".join(category_parts) if category_parts else _CATEGORY_NONE

    # テンプレート選択
    template = _REASON_TEMPLATES.get(list_type, _REASON_TEMPLATES["expanded_only"])
    base_text = template.format(category=category_str)

    # ECCN 付記（1〜3件）
    if eccn_refs:
        eccn_part = "、".join(eccn_refs[:3])
        base_text += f" 対応 ECCN: {eccn_part}。"

    return base_text


def make_short_verdict(item: dict[str, Any], list_type: str) -> str:
    """ハイライトテーブル用の1行説明テキスト（60字以内目安）。"""
    item_ids  = item.get("item_ids") or []
    cat_label = item.get("fefta_category_label") or item.get("list_name") or ""
    rule_sum  = (item.get("rule_summary") or "").strip()

    if cat_label and item_ids:
        label = f"{cat_label} / {item_ids[0]}"
    elif cat_label:
        label = cat_label
    elif item_ids:
        label = item_ids[0]
    else:
        label = ""

    if list_type == "intersection":
        prefix = "両面確認済み"
    elif list_type == "core_only":
        prefix = "直接一致"
    else:
        prefix = "用途関連"

    if rule_sum:
        return f"{prefix}: {rule_sum[:60]}{'…' if len(rule_sum) > 60 else ''}"
    return f"{prefix}: {label}"
