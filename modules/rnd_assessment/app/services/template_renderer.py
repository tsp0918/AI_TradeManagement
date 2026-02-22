from __future__ import annotations

from typing import Dict, Any, List


def _lines(prefix: str, pairs: List[tuple[str, Any]]) -> str:
    out = [prefix]
    for k, v in pairs:
        if v is None:
            continue
        if isinstance(v, list):
            if not v:
                continue
            out.append(f"- {k}: {', '.join(str(x) for x in v)}")
        else:
            out.append(f"- {k}: {v}")
    return "\n".join(out)


def render_use_text(d: Dict[str, Any]) -> str:
    return _lines(
        "[Use Requirements Template]",
        [
            ("Process", d.get("process")),
            ("Product category", d.get("product_category")),
            ("Tech node", d.get("tech_node_nm")),
            ("Application", d.get("application")),
            ("R&D phase", d.get("rd_phase")),
            ("Usage detail", d.get("usage_description")),
            ("Risk flags", _risk_flags_use(d)),
            ("Tags", d.get("tags")),
        ],
    )


def _risk_flags_use(d: Dict[str, Any]) -> str | None:
    flags = []
    if d.get("dual_use_potential"):
        flags.append("dual-use potential")
    if d.get("military_end_use_possible"):
        flags.append("military end-use possible")
    if d.get("surveillance_end_use_possible"):
        flags.append("surveillance end-use possible")
    return ", ".join(flags) if flags else None


def render_end_user_text(d: Dict[str, Any]) -> str:
    return _lines(
        "[End User Requirements Template]",
        [
            ("End user name", d.get("end_user_name")),
            ("End user country", d.get("end_user_country")),
            ("End user type", d.get("end_user_type")),
            ("Intended countries", d.get("intended_countries")),
            ("Retransfer possible", d.get("retransfer_possible")),
            ("Restricted party screened", d.get("restricted_party_screened")),
            ("Screening reference", d.get("screening_reference")),
            ("Notes", d.get("notes")),
            ("Tags", d.get("tags")),
        ],
    )


def render_disclosure_text(d: Dict[str, Any]) -> str:
    """
    Open/Close戦略のための人間可読テキスト（Profileにrawとして保存）
    """
    return _lines(
        "[Disclosure Strategy Template: Photoresist]",
        [
            ("Resist type", d.get("resist_type")),
            ("Target node (nm)", d.get("target_node_nm")),
            ("Application", d.get("application")),
            ("Novelty source", d.get("novelty_source")),
            ("Reproducibility risk", d.get("reproducibility_risk")),
            ("Reverse engineering risk", d.get("reverse_engineering_risk")),
            ("Requires process knowhow", d.get("requires_process_knowhow")),
            ("Strategic importance", d.get("strategic_importance")),
            ("Export control sensitivity", d.get("export_control_sensitivity")),
            ("Military dual-use potential", d.get("military_dual_use_potential")),
            ("Planned patent filing", d.get("planned_patent_filing")),
            ("Planned filing region", d.get("planned_filing_region")),
            ("Publication intent", d.get("publication_intent")),
            ("External collaboration", d.get("external_collaboration")),
            ("Partner countries", d.get("partner_countries")),
            ("NDA in place", d.get("nda_in_place")),
            ("Notes", d.get("notes")),
        ],
    )