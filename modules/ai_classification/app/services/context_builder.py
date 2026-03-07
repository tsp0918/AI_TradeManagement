"""該非判定コンテキスト分析サービス。

品目データの充足度を評価し、ユーザーへのヒアリング項目と
強化されたペイロードを生成する。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional


# ── 判定に効く主要フィールド定義 ───────────────────────────────────────
#   (field_name, 表示ラベル, 重要度: "critical"|"important"|"nice_to_have")
_FIELD_SPECS: list[tuple[str, str, str]] = [
    ("name",            "品目名",                    "critical"),
    ("usage_summary",   "用途概要（AI判定用）",       "critical"),
    ("description",     "詳細説明",                  "important"),
    ("item_class",      "品目クラス",                "important"),
    ("hs_code",         "HSコード",                  "important"),
    ("eccn",            "ECCN",                      "nice_to_have"),
    ("country_of_origin","原産地",                   "nice_to_have"),
    ("bom_json",        "部品構成（BOM）",            "nice_to_have"),
]

# 用途概要の最低文字数（これを下回ると「要補強」と判断）
_USAGE_MIN_CHARS = 30


@dataclass
class ContextReadiness:
    """コンテキスト分析結果。"""
    effective_fields: list[dict]       # 設定済みで判定に効くフィールド
    missing_fields: list[dict]         # 未設定だが判定に効くフィールド
    weak_fields: list[dict]            # 設定はあるが内容が薄いフィールド
    interview_questions: list[str]     # ユーザーへのヒアリング設問
    confidence_hint: str               # "high" | "medium" | "low"
    confirm_summary: str               # 「これで判定しますか？」確認文
    # 補完後ペイロードに追加で渡す extra キー
    extra_context_keys: list[str] = field(default_factory=list)


def analyze_readiness(product: Any, extra_inputs: dict | None = None) -> ContextReadiness:
    """
    品目データ（Product ORM インスタンスまたは dict）と
    ユーザーが追記した extra_inputs を受け取り、充足度を評価する。

    Args:
        product: Product ORM インスタンス or dict
        extra_inputs: フロント側でユーザーが追記したフィールド値
    """
    extra = extra_inputs or {}

    def _get(field_name: str) -> Any:
        extra_val = extra.get(field_name)
        if extra_val and str(extra_val).strip():
            return str(extra_val).strip()
        if isinstance(product, dict):
            return product.get(field_name)
        return getattr(product, field_name, None)

    effective: list[dict] = []
    missing: list[dict] = []
    weak: list[dict] = []

    for fname, label, importance in _FIELD_SPECS:
        val = _get(fname)
        if fname == "bom_json" and val:
            # BOM は JSON 文字列 → 件数を表示
            try:
                items = json.loads(val) if isinstance(val, str) else val
                display = f"{len(items)} 件"
            except Exception:
                display = "（データあり）"
            effective.append({"field": fname, "label": label, "value": display, "importance": importance})
            continue

        if not val or (isinstance(val, str) and not val.strip()):
            entry = {"field": fname, "label": label, "importance": importance}
            if importance in ("critical", "important"):
                missing.append(entry)
        else:
            val_str = str(val).strip()
            # 用途概要が短すぎる場合 → weak
            if fname == "usage_summary" and len(val_str) < _USAGE_MIN_CHARS:
                weak.append({
                    "field": fname,
                    "label": label,
                    "value": val_str,
                    "importance": importance,
                    "hint": f"現在 {len(val_str)} 文字。用途・技術仕様・用途先を具体的に記載すると精度が上がります。",
                })
            else:
                effective.append({
                    "field": fname,
                    "label": label,
                    "value": val_str[:120] + ("…" if len(val_str) > 120 else ""),
                    "importance": importance,
                })

    # ── ヒアリング設問を生成 ──
    questions: list[str] = []
    missing_labels = {e["field"] for e in missing}
    weak_labels = {e["field"] for e in weak}

    if "usage_summary" in missing_labels:
        questions.append(
            "この品目の主な用途・使用目的を教えてください。"
            "（例: 半導体製造工程の露光工程向け ArF 液浸レジスト）"
        )
    elif "usage_summary" in weak_labels:
        questions.append(
            "用途概要をもう少し具体的にご記載ください。"
            "技術規格・動作周波数・最終用途・需要家の業種などが含まれると判定精度が上がります。"
        )

    if "description" in missing_labels:
        questions.append(
            "詳細説明（化学組成・主成分・技術仕様）があれば教えてください。"
        )

    if "item_class" in missing_labels:
        questions.append(
            "品目クラス（SEMICONDUCTOR_MATERIAL / CHEMICAL / ELECTRONIC_COMPONENT / EQUIPMENT など）を選択してください。"
        )

    if "hs_code" in missing_labels:
        questions.append(
            "HS コード（関税分類番号）は分かりますか？（例: 3707.10）"
            " 不明な場合は HS コード判定ボタンで自動判定できます。"
        )

    if "country_of_origin" in missing_labels:
        questions.append(
            "原産地（製造国）を教えてください。（例: JP, US, CN）"
            " 米国原産品・米国技術が含まれる場合は EAR 規制が追加適用されます。"
        )

    # ── 充足度スコア ──
    n_critical = sum(1 for _, _, imp in _FIELD_SPECS if imp == "critical")
    n_critical_ok = sum(
        1 for e in effective + weak
        if e["field"] in {f for f, _, imp in _FIELD_SPECS if imp == "critical"}
    )
    n_important = sum(1 for _, _, imp in _FIELD_SPECS if imp == "important")
    n_important_ok = sum(
        1 for e in effective
        if e["field"] in {f for f, _, imp in _FIELD_SPECS if imp == "important"}
    )

    if n_critical_ok == n_critical and n_important_ok >= n_important - 1:
        confidence = "high"
    elif n_critical_ok >= 1:
        confidence = "medium"
    else:
        confidence = "low"

    # ── 確認メッセージ ──
    eff_labels = [e["label"] for e in effective if e["importance"] in ("critical", "important")]
    if confidence == "high":
        confirm_summary = (
            f"判定に必要な情報（{', '.join(eff_labels)}）が揃っています。"
            " このまま AI 判定を依頼しますか？"
        )
    elif confidence == "medium":
        confirm_summary = (
            f"主要情報（{', '.join(eff_labels)}）は入力済みです。"
            " 一部情報が不足していますが、このまま依頼することができます。"
            " 追加情報を入力するとより精度が上がります。"
        )
    else:
        confirm_summary = (
            "判定に必要な情報が不足しています。"
            " 用途概要と詳細説明を入力してから依頼することをお勧めします。"
        )

    return ContextReadiness(
        effective_fields=effective,
        missing_fields=missing,
        weak_fields=weak,
        interview_questions=questions,
        confidence_hint=confidence,
        confirm_summary=confirm_summary,
    )


def build_enriched_payload(product: Any, extra_inputs: dict | None = None) -> dict:
    """
    product データ + ユーザー追記分を統合して
    ai_validation へ送るリッチなペイロードを生成する。
    """
    extra = extra_inputs or {}

    def _merge(field_name: str) -> Any:
        extra_val = extra.get(field_name)
        if extra_val and str(extra_val).strip():
            return str(extra_val).strip()
        if isinstance(product, dict):
            return product.get(field_name)
        return getattr(product, field_name, None)

    # 用途概要: DB値 + ユーザー追記を結合
    usage_db = (getattr(product, "usage_summary", None) or "").strip()
    usage_extra = (extra.get("usage_supplement") or "").strip()
    if usage_extra:
        usage_combined = f"{usage_db}\n\n【追記情報】\n{usage_extra}".strip()
    else:
        usage_combined = usage_db

    return {
        "name":          _merge("name") or "",
        "description":   _merge("description") or "",
        "usage_summary": usage_combined,
        "item_class":    _merge("item_class"),
        "hs_code":       _merge("hs_code"),
        "eccn":          _merge("eccn"),
        "country_of_origin": _merge("country_of_origin"),
        "bom_json":      _merge("bom_json"),
        "regulation_ai_raw": _merge("regulation_ai_raw"),
    }
