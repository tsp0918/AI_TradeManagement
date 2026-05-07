from __future__ import annotations

from typing import Any, Dict, List

_HIGH_RISK_COUNTRIES = frozenset({"CN", "RU", "KP", "IR", "SY", "BY"})
_MOD_RISK_COUNTRIES = frozenset({"HK", "CU", "VE"})
_MIL_KEYWORDS = frozenset({
    "military", "defense", "defence", "missile", "weapon", "nuclear",
    "radar", "satellite", "space", "warhead", "explosive", "drone",
    "軍事", "軍用", "ミサイル", "核", "衛星", "レーダー", "爆発物",
})

_REC_LABEL_JA: Dict[str, str] = {
    "stop": "要停止 — 法務・安保担当に即時エスカレーション",
    "escalate_to_legal": "法務・安保担当へエスカレーション必須",
    "proceed_with_mitigation": "条件付き進行 — 管理措置を実施のうえ継続",
    "proceed": "進行可 — 定期モニタリング継続",
}

_DIS_STRATEGY_LABEL_JA: Dict[str, str] = {
    "PATENT": "特許公開",
    "TRADE_SECRET": "営業秘密（秘匿）",
    "HYBRID": "ハイブリッド（権利化＋秘匿）",
    "DEFER": "保留・検討中",
}

_SEC_POSTURE_LABEL_JA: Dict[str, str] = {
    "LOW": "低リスク",
    "CONTROLLED": "管理対象",
    "HIGH": "高感度",
    "RESTRICTED": "制限（軍事・高リスク国）",
    "ESCALATE": "要エスカレーション",
}

_REASON_JA: Dict[str, str] = {
    "strategic_importance=high": "戦略的重要度：高",
    "strategic_importance=critical": "戦略的重要度：最重要",
    "reproducibility_risk=high": "再現リスク：高（競合による複製が容易）",
    "reverse_engineering_risk=high": "リバースエンジニアリングリスク：高",
    "planned_patent_filing=true": "特許出願予定あり",
    "publication_intent=true": "論文公開意図あり",
    "dual_use_potential": "軍民両用の可能性あり",
    "military_end_use_possible": "軍事エンドユーズの可能性あり",
    "surveillance_end_use_possible": "監視用途の可能性あり",
    "export_control_sensitivity=true": "輸出管理感度あり",
    "military_dual_use_potential=true": "軍民両用の可能性（開示情報より）",
    "restricted_party_not_screened": "制限当事者スクリーニング未実施",
    "end_user_country=CN": "エンドユーザー国：中国",
    "end_user_country=RU": "エンドユーザー国：ロシア",
    "end_user_country=KP": "エンドユーザー国：北朝鮮",
    "end_user_country=IR": "エンドユーザー国：イラン",
    "end_user_country=SY": "エンドユーザー国：シリア",
    "end_user_country=BY": "エンドユーザー国：ベラルーシ",
    "high reproducibility/reverse-engineering risk and high strategic importance":
        "再現・リバースリスク高 かつ 戦略重要度高",
    "planned patent but high reproducibility/reverse-engineering risk":
        "特許予定だが再現リスク高 → ハイブリッド推奨",
    "planned patent filing and risks not high":
        "特許出願予定 かつ リスク許容範囲内",
    "publication intent but high strategic importance":
        "論文公開意図あり かつ 戦略重要度高 → 保留推奨",
    "insufficient input to decide": "判断に必要な情報が不足しています",
    "military adjacency or high-risk country + sensitivity":
        "軍事近接 または 高リスク国 かつ 輸出感度あり",
    "dual-use/export sensitivity or moderate-risk country":
        "軍民両用・輸出感度あり または 中リスク国",
    "restricted party not screened": "制限当事者スクリーニング未実施",
}


def _ja(reason: str) -> str:
    return _REASON_JA.get(reason, reason)


def classify_disclosure_strategy(disclosure_t: dict | None, use_t: dict | None) -> Dict[str, Any]:
    """開示戦略（Open/Close）粗分類"""
    disclosure_t = disclosure_t or {}
    use_t = use_t or {}
    reasons: List[str] = []

    reproducibility = disclosure_t.get("reproducibility_risk")
    reverse_eng = disclosure_t.get("reverse_engineering_risk")
    strategic = disclosure_t.get("strategic_importance")
    planned_patent = disclosure_t.get("planned_patent_filing")
    pub_intent = disclosure_t.get("publication_intent")
    novelty = disclosure_t.get("novelty_source") or []

    if strategic in ("high", "critical"):
        reasons.append(f"strategic_importance={strategic}")
    if reproducibility == "high":
        reasons.append("reproducibility_risk=high")
    if reverse_eng == "high":
        reasons.append("reverse_engineering_risk=high")
    if planned_patent is True:
        reasons.append("planned_patent_filing=true")
    if pub_intent is True:
        reasons.append("publication_intent=true")
    if novelty:
        reasons.append(f"novelty_source={','.join(novelty)}")

    if (reproducibility == "high" or reverse_eng == "high") and strategic in ("high", "critical"):
        strat, conf = "TRADE_SECRET", "high"
        raw_reason = "high reproducibility/reverse-engineering risk and high strategic importance"
    elif planned_patent is True and (reproducibility == "high" or reverse_eng == "high"):
        strat, conf = "HYBRID", "medium"
        raw_reason = "planned patent but high reproducibility/reverse-engineering risk"
    elif planned_patent is True and reproducibility != "high" and reverse_eng != "high":
        strat, conf = "PATENT", "medium"
        raw_reason = "planned patent filing and risks not high"
    elif pub_intent is True and strategic in ("high", "critical"):
        strat, conf = "DEFER", "medium"
        raw_reason = "publication intent but high strategic importance"
    else:
        strat, conf = "DEFER", "low"
        raw_reason = "insufficient input to decide"

    if not reasons:
        reasons = [raw_reason]

    return {
        "disclosure_strategy": strat,
        "label_ja": _DIS_STRATEGY_LABEL_JA.get(strat, strat),
        "confidence": conf,
        "reasons": reasons,
        "reasons_ja": [_ja(r) for r in reasons],
    }


def classify_security_posture(use_t: dict | None, end_t: dict | None, disclosure_t: dict | None) -> Dict[str, Any]:
    """安保貿易管理側の扱い方分類"""
    use_t = use_t or {}
    end_t = end_t or {}
    disclosure_t = disclosure_t or {}
    reasons: List[str] = []

    dual_use = bool(use_t.get("dual_use_potential"))
    mil = bool(use_t.get("military_end_use_possible"))
    surv = bool(use_t.get("surveillance_end_use_possible"))
    end_country = (end_t.get("end_user_country") or "").upper()
    screened = end_t.get("restricted_party_screened")
    export_sens = disclosure_t.get("export_control_sensitivity")
    military_adj = disclosure_t.get("military_dual_use_potential")
    strategic = disclosure_t.get("strategic_importance")

    if dual_use:
        reasons.append("dual_use_potential")
    if mil:
        reasons.append("military_end_use_possible")
    if surv:
        reasons.append("surveillance_end_use_possible")
    if export_sens is True:
        reasons.append("export_control_sensitivity=true")
    if military_adj is True:
        reasons.append("military_dual_use_potential=true")
    if strategic in ("high", "critical"):
        reasons.append(f"strategic_importance={strategic}")
    if end_country in _HIGH_RISK_COUNTRIES:
        reasons.append(f"end_user_country={end_country}")
    if screened is False:
        reasons.append("restricted_party_not_screened")

    if mil or military_adj or (end_country in _HIGH_RISK_COUNTRIES and (dual_use or export_sens)):
        posture = "RESTRICTED"
        raw_reason = "military adjacency or high-risk country + sensitivity"
    elif dual_use or export_sens or end_country in _MOD_RISK_COUNTRIES:
        posture = "HIGH"
        raw_reason = "dual-use/export sensitivity or moderate-risk country"
    elif screened is False:
        posture = "ESCALATE"
        raw_reason = "restricted party not screened"
    elif strategic in ("high", "critical"):
        posture = "CONTROLLED"
        raw_reason = f"strategic_importance={strategic}"
    else:
        posture = "LOW"
        raw_reason = ""

    if not reasons:
        reasons = [raw_reason] if raw_reason else []

    return {
        "security_posture": posture,
        "label_ja": _SEC_POSTURE_LABEL_JA.get(posture, posture),
        "reasons": reasons,
        "reasons_ja": [_ja(r) for r in reasons],
    }


def score_demo(profile) -> Dict[str, Any]:
    """15+ルールによるリスクスコアリング。regulatory_risk は独立次元として計算。"""
    use_t = profile.use_template_json or {}
    end_t = profile.end_user_template_json or {}
    disclosure_t = profile.disclosure_template_json or {}

    tech_risk = 20
    use_risk = 25
    end_user_risk = 30
    regulatory_risk = 25

    factors: List[str] = []
    rules: List[dict] = []

    # ── 技術リスク ────────────────────────────────────────────
    if use_t.get("dual_use_potential"):
        tech_risk += 30
        use_risk += 15
        factors.append("dual_use_potential")
        rules.append({"rule_id": "DUAL_USE_POTENTIAL", "severity": "medium"})

    if use_t.get("military_end_use_possible"):
        tech_risk += 40
        use_risk += 20
        regulatory_risk += 30
        factors.append("military_end_use_possible")
        rules.append({"rule_id": "MIL_END_USE_POSSIBLE", "severity": "high"})

    if use_t.get("surveillance_end_use_possible"):
        tech_risk += 25
        use_risk += 15
        factors.append("surveillance_end_use_possible")
        rules.append({"rule_id": "SURVEILLANCE_END_USE_POSSIBLE", "severity": "medium"})

    if disclosure_t.get("military_dual_use_potential"):
        tech_risk += 20
        regulatory_risk += 20
        factors.append("military_dual_use_potential")
        rules.append({"rule_id": "MIL_DUAL_USE_POTENTIAL", "severity": "high"})

    strategic = disclosure_t.get("strategic_importance")
    if strategic == "critical":
        tech_risk += 20
        regulatory_risk += 15
        factors.append("strategic_importance_critical")
        rules.append({"rule_id": "STRATEGIC_CRITICAL", "severity": "medium"})
    elif strategic == "high":
        tech_risk += 10
        regulatory_risk += 10
        factors.append("strategic_importance_high")
        rules.append({"rule_id": "STRATEGIC_HIGH", "severity": "low"})

    tech_tags = [t.lower() for t in (use_t.get("tags") or [])]
    usage_desc = (use_t.get("usage_description") or "").lower()
    if any(kw in tech_tags or kw in usage_desc for kw in _MIL_KEYWORDS):
        tech_risk += 20
        factors.append("military_keyword_detected")
        rules.append({"rule_id": "MIL_KEYWORD_DETECTED", "severity": "medium"})

    if disclosure_t.get("reproducibility_risk") == "high":
        tech_risk += 10
        factors.append("reproducibility_risk_high")
        rules.append({"rule_id": "REPRODUCIBILITY_HIGH", "severity": "low"})

    # ── 用途リスク ────────────────────────────────────────────
    phase = (use_t.get("rd_phase") or "").lower()
    if "mass" in phase:
        use_risk += 20
        factors.append("phase_mass_production")
        rules.append({"rule_id": "PHASE_MASS_PRODUCTION", "severity": "medium"})
    elif "pilot" in phase:
        use_risk += 10
        factors.append("phase_pilot")
        rules.append({"rule_id": "PHASE_PILOT", "severity": "low"})

    if end_t.get("retransfer_possible"):
        use_risk += 15
        regulatory_risk += 10
        factors.append("retransfer_possible")
        rules.append({"rule_id": "RETRANSFER_POSSIBLE", "severity": "medium"})

    intended = [c.upper() for c in (end_t.get("intended_countries") or [])]
    hr_intended = [c for c in intended if c in _HIGH_RISK_COUNTRIES]
    if hr_intended:
        use_risk += 20
        regulatory_risk += 15
        factors.append(f"intended_high_risk_country:{','.join(hr_intended)}")
        rules.append({"rule_id": "INTENDED_HIGH_RISK_COUNTRY", "severity": "high"})

    partner_countries = [c.upper() for c in (disclosure_t.get("partner_countries") or [])]
    hr_partners = [c for c in partner_countries if c in _HIGH_RISK_COUNTRIES]
    if hr_partners:
        use_risk += 15
        regulatory_risk += 10
        factors.append(f"partner_high_risk_country:{','.join(hr_partners)}")
        rules.append({"rule_id": "PARTNER_HIGH_RISK_COUNTRY", "severity": "medium"})

    # ── 需要者リスク ───────────────────────────────────────────
    if end_t.get("restricted_party_screened") is False:
        end_user_risk += 25
        regulatory_risk += 15
        factors.append("restricted_party_not_screened")
        rules.append({"rule_id": "RPS_NOT_SCREENED", "severity": "high"})

    end_country = (end_t.get("end_user_country") or "").upper()
    if end_country in ("CN", "RU"):
        end_user_risk += 40
        regulatory_risk += 20
        factors.append(f"end_user_country_high_risk:{end_country}")
        rules.append({"rule_id": "END_COUNTRY_HIGH_RISK", "severity": "high"})
    elif end_country in ("KP", "IR", "SY", "BY"):
        end_user_risk += 50
        regulatory_risk += 30
        factors.append(f"end_user_country_critical_risk:{end_country}")
        rules.append({"rule_id": "END_COUNTRY_CRITICAL_RISK", "severity": "critical"})
    elif end_country == "HK":
        end_user_risk += 15
        regulatory_risk += 10
        factors.append("end_user_country_moderate_risk:HK")
        rules.append({"rule_id": "END_COUNTRY_MODERATE_RISK", "severity": "medium"})

    # ── 法規制リスク（独立次元） ─────────────────────────────────
    if disclosure_t.get("export_control_sensitivity"):
        regulatory_risk += 20
        factors.append("export_control_sensitivity")
        rules.append({"rule_id": "EXPORT_CTRL_SENSITIVITY", "severity": "high"})

    if disclosure_t.get("publication_intent") and strategic in ("high", "critical"):
        regulatory_risk += 10
        factors.append("publication_with_high_strategic_importance")
        rules.append({"rule_id": "PUB_INTENT_HIGH_STRATEGIC", "severity": "medium"})

    # キャップ
    tech_risk = min(100, tech_risk)
    use_risk = min(100, use_risk)
    end_user_risk = min(100, end_user_risk)
    regulatory_risk = min(100, regulatory_risk)

    overall = int(0.35 * tech_risk + 0.25 * use_risk + 0.25 * end_user_risk + 0.15 * regulatory_risk)
    overall = max(0, min(100, overall))

    if overall >= 80:
        risk_level, rec = "critical", "stop"
    elif overall >= 60:
        risk_level, rec = "high", "escalate_to_legal"
    elif overall >= 40:
        risk_level, rec = "medium", "proceed_with_mitigation"
    else:
        risk_level, rec = "low", "proceed"

    disclosure_result = classify_disclosure_strategy(disclosure_t, use_t)
    security_result = classify_security_posture(use_t, end_t, disclosure_t)

    return {
        "overall_score": overall,
        "risk_level": risk_level,
        "recommendation": rec,
        "recommendation_ja": _REC_LABEL_JA.get(rec, rec),
        "tech_risk_score": tech_risk,
        "use_risk_score": use_risk,
        "end_user_risk_score": end_user_risk,
        "regulatory_risk_score": regulatory_risk,
        "rules_triggered": rules or None,
        "top_factors": factors or None,
        "disclosure_result": disclosure_result,
        "security_result": security_result,
    }
