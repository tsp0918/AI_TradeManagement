from __future__ import annotations

from typing import Any, Dict, List, Optional


def classify_disclosure_strategy(disclosure_t: dict | None, use_t: dict | None) -> Dict[str, Any]:
    """
    フォトレジスト前提の「Open/Close戦略」粗分類
    出力:
      {
        "disclosure_strategy": "PATENT|TRADE_SECRET|HYBRID|DEFER",
        "confidence": "low|medium|high",
        "reasons": [...],
      }
    """
    disclosure_t = disclosure_t or {}
    use_t = use_t or {}
    reasons: List[str] = []

    reproducibility = disclosure_t.get("reproducibility_risk")
    reverse_eng = disclosure_t.get("reverse_engineering_risk")
    strategic = disclosure_t.get("strategic_importance")
    planned_patent = disclosure_t.get("planned_patent_filing")
    pub_intent = disclosure_t.get("publication_intent")
    novelty = disclosure_t.get("novelty_source") or []

    # 重要な要因
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

    # ルール（粗いが説明しやすい）
    # 1) 再現容易×戦略重要 → 秘匿寄り
    if (reproducibility == "high" or reverse_eng == "high") and strategic in ("high", "critical"):
        return {
            "disclosure_strategy": "TRADE_SECRET",
            "confidence": "high",
            "reasons": reasons or ["high reproducibility/reverse-engineering risk and high strategic importance"],
        }

    # 2) 特許したい + 再現性高い → ハイブリッド（権利化しつつノウハウ秘匿）
    if planned_patent is True and (reproducibility == "high" or reverse_eng == "high"):
        return {
            "disclosure_strategy": "HYBRID",
            "confidence": "medium",
            "reasons": reasons or ["planned patent but high reproducibility/reverse-engineering risk"],
        }

    # 3) 特許したい + 再現性が高すぎない → 特許
    if planned_patent is True and reproducibility != "high" and reverse_eng != "high":
        return {
            "disclosure_strategy": "PATENT",
            "confidence": "medium",
            "reasons": reasons or ["planned patent filing and risks not high"],
        }

    # 4) 論文公開意図あり + しかし戦略重要/輸出管理感度あり → 制御公開（DEFER寄り）
    if pub_intent is True and strategic in ("high", "critical"):
        return {
            "disclosure_strategy": "DEFER",
            "confidence": "medium",
            "reasons": reasons or ["publication intent but high strategic importance"],
        }

    # 5) 情報不足
    return {
        "disclosure_strategy": "DEFER",
        "confidence": "low",
        "reasons": reasons or ["insufficient input to decide"],
    }


def classify_security_posture(use_t: dict | None, end_t: dict | None, disclosure_t: dict | None) -> Dict[str, Any]:
    """
    安保貿易管理側の“扱い方”粗分類（輸出管理と同じ土俵に乗せる）
    出力:
      {"security_posture":"LOW|CONTROLLED|HIGH|RESTRICTED|ESCALATE", "reasons":[...]}
    """
    use_t = use_t or {}
    end_t = end_t or {}
    disclosure_t = disclosure_t or {}
    reasons: List[str] = []

    # inputs
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
    if end_country in ("CN", "RU", "IR", "KP"):
        reasons.append(f"end_user_country={end_country}")
    if screened is False:
        reasons.append("restricted_party_not_screened")

    # rules
    if mil or military_adj or (end_country in ("CN", "RU", "IR", "KP") and (dual_use or export_sens)):
        return {"security_posture": "RESTRICTED", "reasons": reasons or ["military adjacency or high-risk country + sensitivity"]}

    if dual_use or export_sens or end_country in ("HK",):
        return {"security_posture": "HIGH", "reasons": reasons or ["dual-use/export sensitivity or moderate-risk country"]}

    if screened is False:
        return {"security_posture": "ESCALATE", "reasons": reasons or ["restricted party not screened"]}

    return {"security_posture": "CONTROLLED" if strategic in ("high", "critical") else "LOW", "reasons": reasons}


def score_demo(profile) -> Dict[str, Any]:
    """
    既存PoCスコアリングを維持しつつ、disclosure/securityの分類を追加する。
    （UI routerの _score_demo をこの関数に寄せるのが理想だが、まずはサービス層に用意）
    """
    use_t = profile.use_template_json or {}
    end_t = profile.end_user_template_json or {}
    disclosure_t = profile.disclosure_template_json or {}

    # --- 기존の簡易スコア ---
    tech_risk = 20
    use_risk = 25
    end_user_risk = 30
    regulatory_risk = 25

    factors: List[str] = []
    rules: List[dict] = []

    if use_t.get("dual_use_potential"):
        tech_risk += 30
        factors.append("dual_use_potential")
        rules.append({"rule_id": "DUAL_USE_POTENTIAL", "severity": "medium"})

    if use_t.get("military_end_use_possible"):
        tech_risk += 40
        factors.append("military_end_use_possible")
        rules.append({"rule_id": "MIL_END_USE_POSSIBLE", "severity": "high"})

    if use_t.get("surveillance_end_use_possible"):
        tech_risk += 25
        factors.append("surveillance_end_use_possible")
        rules.append({"rule_id": "SURVEILLANCE_END_USE_POSSIBLE", "severity": "medium"})

    # end-user adjustments
    if end_t.get("restricted_party_screened") is False:
        end_user_risk += 20
        factors.append("restricted_party_not_screened")
        rules.append({"rule_id": "RPS_NOT_SCREENED", "severity": "high"})

    # phase
    phase = (use_t.get("rd_phase") or "").lower()
    if "mass" in phase:
        use_risk += 20
        factors.append("phase_mass_production")
    elif "pilot" in phase:
        use_risk += 10
        factors.append("phase_pilot")

    tech_risk = min(100, tech_risk)
    use_risk = min(100, use_risk)
    end_user_risk = min(100, end_user_risk)
    regulatory_risk = int(min(100, max(tech_risk, end_user_risk) * 0.9))

    overall = int(0.35 * tech_risk + 0.25 * use_risk + 0.25 * end_user_risk + 0.15 * regulatory_risk)
    overall = max(0, min(100, overall))

    if overall >= 80:
        risk_level = "critical"
        rec = "stop"
    elif overall >= 60:
        risk_level = "high"
        rec = "escalate_to_legal"
    elif overall >= 40:
        risk_level = "medium"
        rec = "proceed_with_mitigation"
    else:
        risk_level = "low"
        rec = "proceed"

    disclosure_result = classify_disclosure_strategy(disclosure_t, use_t)
    security_result = classify_security_posture(use_t, end_t, disclosure_t)

    return {
        "overall_score": overall,
        "risk_level": risk_level,
        "recommendation": rec,
        "tech_risk_score": tech_risk,
        "use_risk_score": use_risk,
        "end_user_risk_score": end_user_risk,
        "regulatory_risk_score": regulatory_risk,
        "rules_triggered": rules or None,
        "top_factors": factors or None,
        "disclosure_result": disclosure_result,
        "security_result": security_result,
    }