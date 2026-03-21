"""
deemed_export.py — みなし輸出 該当性スクリーニング

外為法施行規則 第17条の3 に基づく3カテゴリ判定。

カテゴリA: 外国国籍保有者への規制技術提供（国籍リスクによる重み付け）
カテゴリB: 懸念国企業との二重雇用・兼職
カテゴリC: 非居住者（在日5年未満の外国人）への技術開示

判定結果: high / medium / low / none
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.models.personnel import Personnel

# 輸出禁止国・特別管理対象国（外為法 輸出令 別表第3）
_PROHIBITED_NATIONALITIES = frozenset({"KP", "IR", "SY", "CU"})

# 重点審査対象国（ウォッチリスト的位置付け）
_WATCH_NATIONALITIES = frozenset({"RU", "BY", "CN"})

# 二重雇用懸念国（上記と同じ範囲）
_DUAL_EMPLOY_CONCERN = _PROHIBITED_NATIONALITIES | _WATCH_NATIONALITIES


def screen_person(person: Personnel) -> dict[str, Any]:
    """
    みなし輸出 3カテゴリ判定を実行し、結果 dict を返す。

    Returns:
        {
            "category": "A" | "B" | "C" | None,
            "risk": "high" | "medium" | "low" | "none",
            "reasons": list[str],
            "requires_permit_check": bool,
            "recommendation": str,
        }
    """
    nat = (person.nationality or "").strip().upper()
    emp_country = (person.dual_employer_country or "").strip().upper()
    years = person.years_in_japan if person.years_in_japan is not None else 99.0
    has_tech = bool(person.tech_access_eccn or person.tech_access_fefta)

    category: str | None = None
    risk = "none"
    reasons: list[str] = []

    # ── Category A: 国籍による リスク ────────────────────────────────
    if nat and nat != "JP":
        if nat in _PROHIBITED_NATIONALITIES:
            category = "A"
            risk = "high"
            reasons.append(f"国籍 {nat} は輸出禁止国/特別管理対象国（外為法別表第3）")
        elif nat in _WATCH_NATIONALITIES:
            category = "A"
            risk = "medium"
            reasons.append(f"国籍 {nat} は重点審査対象国")
        else:
            # その他外国籍 → 在留年数確認へ
            pass

    # ── Category B: 二重雇用リスク ────────────────────────────────────
    if person.dual_employment_flag:
        if emp_country in _DUAL_EMPLOY_CONCERN:
            cat_b_risk = "high" if emp_country in _PROHIBITED_NATIONALITIES else "medium"
            if _risk_level(cat_b_risk) > _risk_level(risk):
                risk = cat_b_risk
            if category is None:
                category = "B"
            reasons.append(
                f"懸念国企業（{emp_country}）との兼職・二重雇用 — "
                f"{person.dual_employer_name or '企業名不明'}"
            )
        else:
            if category is None:
                category = "B"
            if risk == "none":
                risk = "low"
            reasons.append("外国企業との二重雇用あり（懸念国以外）")

    # ── Category C: 非居住者への技術開示リスク ───────────────────────
    if nat and nat != "JP" and years < 5:
        if category is None:
            category = "C"
        if risk == "none":
            risk = "low"
        reasons.append(
            f"在日年数 {years:.1f} 年（5年未満 → 非居住者として管理が必要）"
        )

    # 技術アクセスがある場合はリスクを一段引き上げ
    if has_tech and risk in ("none", "low"):
        if risk == "none":
            risk = "low"
        elif risk == "low":
            risk = "medium"
        tech_label = " / ".join(filter(None, [
            person.tech_access_eccn, person.tech_access_fefta
        ]))
        reasons.append(f"規制技術へのアクセスあり（{tech_label}）")

    # ── 推奨アクション ────────────────────────────────────────────────
    rec_map = {
        "high":   "安全保障輸出管理担当者へ即時エスカレーション。技術提供前に輸出許可確認が必要。",
        "medium": "みなし輸出確認フォームを提出。技術提供の範囲・内容を書面化してください。",
        "low":    "在留証明書を確認し、技術提供の記録を保管してください。",
        "none":   "現時点で特段の管理措置は不要です。",
    }

    return {
        "category":             category,
        "risk":                 risk,
        "reasons":              reasons,
        "requires_permit_check": risk in ("high", "medium"),
        "recommendation":       rec_map[risk],
    }


def run_screening(person: Personnel) -> Personnel:
    """スクリーニングを実行し、結果を person オブジェクトに書き込んで返す。"""
    result = screen_person(person)
    person.deemed_export_category = result["category"]
    person.deemed_export_risk     = result["risk"]
    person.deemed_export_reason   = json.dumps(result["reasons"], ensure_ascii=False)
    person.screened_at            = datetime.utcnow()
    return person


def _risk_level(risk: str) -> int:
    return {"none": 0, "low": 1, "medium": 2, "high": 3}.get(risk, 0)
