from typing import Any, Dict, List, Tuple


HIGH_RISK_COUNTRIES = {"cn", "china", "ru", "russia", "ir", "iran", "kp", "north korea"}


def _norm(text: str | None) -> str:
    return (text or "").lower()


def run_rules(profile: Dict[str, Any], items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    rules: List[Dict[str, Any]] = []
    factors: List[str] = []

    use_text = _norm(profile.get("use_requirements_raw"))
    end_user_text = _norm(profile.get("end_user_requirements_raw"))

    # Use: military/surveillance keywords (PoC)
    military_keywords = ["military", "weapon", "missile", "defense", "surveillance", "drone"]
    if any(k in use_text for k in military_keywords):
        rules.append({"rule_id": "USE_MILITARY_KEYWORD_HIT", "severity": "high", "evidence": "use_requirements_raw"})
        factors.append("use:military_keyword_hit")

    # End-user: high risk country mention
    if any(k in end_user_text for k in HIGH_RISK_COUNTRIES):
        rules.append({"rule_id": "END_USER_HIGH_RISK_COUNTRY", "severity": "high", "evidence": "end_user_requirements_raw"})
        factors.append("end_user:high_risk_country")

    # Item flags (export_control_flags contains candidate etc.)
    for it in items:
        flags = it.get("export_control_flags") or []
        if any("candidate" in str(x) for x in flags):
            rules.append({
                "rule_id": "ITEM_EXPORT_CONTROL_FLAG",
                "severity": "medium",
                "evidence": f"external_item_id={it.get('external_item_id')}",
            })
            factors.append("item:export_control_flag")

    return rules, factors
