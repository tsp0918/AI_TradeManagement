"""
platform_core.ontology.rules.eu_dual_use_checker
==================================================
EU Dual-Use Regulation チェッカー。

根拠法令:
  - EU Dual-Use Regulation 2021/821 (Regulation (EU) 2021/821)
    — 2021年9月9日施行。旧 Regulation (EC) 428/2009 の改正・統合版
  - Annex I   — EU Dual-Use Control List（EU規制品リスト）
  - Annex II  — EU General Export Authorisations (GEA: EU001〜EU008)
  - Annex IV  — 加盟国内部管理移転のみ許可必要品目

判定フロー:
  1. ECCN カテゴリ → EU Annex I カテゴリ番号を特定（ECCN 先頭数字 ≈ EU カテゴリ）
  2. 仕向地 → EU General Export Authorisation (GEA) 適用可否確認
  3. 規制上の取扱い判定 (GEA / Individual License / Intra-EU)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── EU Dual-Use Annex I カテゴリ ────────────────────────────────────────────
EU_CATEGORY_LABELS: dict[str, str] = {
    "0": "Cat. 0 — Nuclear Materials, Facilities and Equipment",
    "1": "Cat. 1 — Special Materials and Related Equipment",
    "2": "Cat. 2 — Materials Processing",
    "3": "Cat. 3 — Electronics",
    "4": "Cat. 4 — Computers",
    "5": "Cat. 5 — Telecommunications and Information Security",
    "6": "Cat. 6 — Sensors and Lasers",
    "7": "Cat. 7 — Navigation and Avionics",
    "8": "Cat. 8 — Marine",
    "9": "Cat. 9 — Aerospace and Propulsion",
}

# ── EU General Export Authorisations (GEA: Annex II) ────────────────────────
GEA_LABELS: dict[str, str] = {
    "EU001": "EU001 — Export to Australia, Canada, Japan, NZ, Norway, Switzerland, UK, USA（主要同盟国向け包括許可）",
    "EU002": "EU002 — Re-export of Community Goods（EU域内商品の再輸出）",
    "EU003": "EU003 — Export after Repair/Replacement（修理・交換後の再輸出）",
    "EU004": "EU004 — Temporary Export for Exhibition/Fair（見本市・展示会向け一時輸出）",
    "EU005": "EU005 — Telecommunications（電気通信品目の特定向け輸出）",
    "EU006": "EU006 — Chemical Precursors（化学前駆物質の特定向け輸出）",
    "EU007": "EU007 — Intra-group Technology/Software（グループ内技術・ソフトウェア移転）",
    "EU008": "EU008 — Encryption（暗号品目 — mass-market 向け）",
}

# ── EU001 適格国（Annex II Part 1 Section A）────────────────────────────────
_EU001_COUNTRIES = {"AU", "CA", "JP", "NZ", "NO", "CH", "GB", "US"}

# ── EU 加盟国（intra-EU 移転）────────────────────────────────────────────────
_EU_MEMBER_STATES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE",
    "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT",
    "RO", "SK", "SI", "ES", "SE",
}

# ── Annex IV 品目（EU域内でも個別許可が必要） ───────────────────────────────
# （核・生化学・ミサイル・軍事転用可能な特に機微な品目）
_ANNEX_IV_ECCN_PREFIXES = {"0A", "0B", "0C", "1C351", "1C352", "1C353", "2B352", "9A012"}

# ── 高規制リスク国（EU 制裁・懸念国）──────────────────────────────────────
_HIGH_RISK_DESTINATIONS = {"RU", "BY", "KP", "IR", "SY", "CU", "SD"}

# ── 暗号品目（EU008 対象） ─────────────────────────────────────────────────
_ENCRYPTION_CATEGORY = "5"  # Cat. 5 Part 2 (Information Security)


@dataclass
class EUDualUseContext:
    destination_country:      str
    eccn:                     Optional[str]    # EAR ECCN（EU カテゴリへの参照用）
    end_user_type:            str = "unknown"  # commercial / government / military
    is_intra_eu_transfer:     bool = False
    evaluated_at:             str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class EUDualUseJudgment:
    destination_country:      str
    eccn:                     Optional[str]
    eu_category:              Optional[str]    # "0"〜"9" or None
    eu_category_label:        Optional[str]
    is_eu_controlled:         bool
    is_intra_eu:              bool
    applicable_gea:           list[str]
    gea_labels:               list[str]
    verdict:                  str    # NOT_CONTROLLED / GEA / INDIVIDUAL_LICENSE / PROHIBITED
    verdict_label:            str
    rationale:                list[str]
    recommended_actions:      list[str]
    evaluated_at:             str

    def to_dict(self) -> dict[str, Any]:
        return {
            "destination_country":  self.destination_country,
            "eccn":                 self.eccn,
            "eu_category":          self.eu_category,
            "eu_category_label":    self.eu_category_label,
            "is_eu_controlled":     self.is_eu_controlled,
            "is_intra_eu":          self.is_intra_eu,
            "applicable_gea":       self.applicable_gea,
            "gea_labels":           self.gea_labels,
            "verdict":              self.verdict,
            "verdict_label":        self.verdict_label,
            "rationale":            self.rationale,
            "recommended_actions":  self.recommended_actions,
            "evaluated_at":         self.evaluated_at,
        }


def _eccn_to_eu_category(eccn: Optional[str]) -> Optional[str]:
    """ECCN の先頭数字から EU カテゴリ番号を推定する。"""
    if not eccn:
        return None
    first = eccn.strip()[0]
    if first.isdigit():
        return first
    return None


def _is_annex_iv(eccn: Optional[str]) -> bool:
    if not eccn:
        return False
    e = eccn.upper().strip()
    for prefix in _ANNEX_IV_ECCN_PREFIXES:
        if e.startswith(prefix):
            return True
    return False


def evaluate_eu_dual_use(ctx: EUDualUseContext) -> EUDualUseJudgment:
    """EU Dual-Use Regulation 2021/821 に基づく輸出管理判定。"""
    country = ctx.destination_country.upper().strip()
    eu_cat = _eccn_to_eu_category(ctx.eccn)
    eu_cat_label = EU_CATEGORY_LABELS.get(eu_cat) if eu_cat else None

    is_intra_eu = country in _EU_MEMBER_STATES or ctx.is_intra_eu_transfer
    is_annex_iv = _is_annex_iv(ctx.eccn)
    is_high_risk = country in _HIGH_RISK_DESTINATIONS
    is_encryption = eu_cat == _ENCRYPTION_CATEGORY

    applicable_gea: list[str] = []
    rationale: list[str] = []
    actions: list[str] = []

    # ── 規制対象かどうか ──
    is_controlled = eu_cat is not None and ctx.eccn is not None

    if not is_controlled:
        verdict = "NOT_CONTROLLED"
        verdict_label = "🟢 EU Dual-Use 規制対象外 — EAR99 相当品目"
        rationale.append("ECCN が未入力または EAR99 相当のため、EU Dual-Use Annex I 非掲載品目と推定されます。")
        rationale.append("ただし、EU 規制当局による包括規制（Catch-all、Art.4-5）の適用可能性を確認してください。")
        actions.append("EU Catch-all 規制（Art.4: WMD懸念/Art.5: 軍事用途懸念）の確認を行ってください。")
        actions.append("加盟国税関への事前相談を検討してください（不明な場合）。")
        return EUDualUseJudgment(
            destination_country=country, eccn=ctx.eccn, eu_category=eu_cat,
            eu_category_label=eu_cat_label, is_eu_controlled=False, is_intra_eu=is_intra_eu,
            applicable_gea=[], gea_labels=[], verdict=verdict, verdict_label=verdict_label,
            rationale=rationale, recommended_actions=actions, evaluated_at=ctx.evaluated_at,
        )

    # ── EU 域内移転 ──
    if is_intra_eu and not is_annex_iv:
        verdict = "NOT_CONTROLLED"
        verdict_label = "🟢 EU 域内移転 — Annex IV 非対象品目（原則許可不要）"
        rationale.append(f"仕向地 {country} は EU 加盟国です。Annex IV 非掲載品目は域内移転に許可不要です。")
        actions.append("輸出記録（Article 26: 5年間保存）を維持してください。")
        return EUDualUseJudgment(
            destination_country=country, eccn=ctx.eccn, eu_category=eu_cat,
            eu_category_label=eu_cat_label, is_eu_controlled=True, is_intra_eu=True,
            applicable_gea=[], gea_labels=[], verdict=verdict, verdict_label=verdict_label,
            rationale=rationale, recommended_actions=actions, evaluated_at=ctx.evaluated_at,
        )

    if is_intra_eu and is_annex_iv:
        verdict = "INDIVIDUAL_LICENSE"
        verdict_label = "🔴 個別輸出許可が必要 — Annex IV 品目（EU 域内でも許可必要）"
        rationale.append("Annex IV 掲載品目は EU 域内移転においても加盟国個別許可が必要です。")
        actions.append("輸出元加盟国の管轄当局（日本の場合: 経済産業省 安全保障貿易管理課）に許可申請してください。")
        return EUDualUseJudgment(
            destination_country=country, eccn=ctx.eccn, eu_category=eu_cat,
            eu_category_label=eu_cat_label, is_eu_controlled=True, is_intra_eu=True,
            applicable_gea=[], gea_labels=[], verdict=verdict, verdict_label=verdict_label,
            rationale=rationale, recommended_actions=actions, evaluated_at=ctx.evaluated_at,
        )

    # ── EU 域外輸出 ──
    rationale.append(f"品目は EU Dual-Use Annex I {eu_cat_label} に該当する可能性があります。")

    # 制裁国・高リスク国
    if is_high_risk:
        verdict = "PROHIBITED"
        verdict_label = f"🚫 輸出禁止 / 個別許可必要 — {country} は EU 制裁・懸念国"
        rationale.append(f"仕向地 {country} に対し EU 制裁規則が適用されます（2022年以降 RU/BY 強化）。")
        rationale.append("Dual-Use 品目の輸出は原則禁止または厳格な許可審査対象です。")
        actions.append("EU 制裁規則（Council Regulation (EU) 2022/328 等）を確認してください。")
        actions.append("加盟国所轄当局への事前相談を強く推奨します。")
        actions.append("取引を即座に停止し、法務部門に報告してください。")
        return EUDualUseJudgment(
            destination_country=country, eccn=ctx.eccn, eu_category=eu_cat,
            eu_category_label=eu_cat_label, is_eu_controlled=True, is_intra_eu=False,
            applicable_gea=[], gea_labels=[], verdict=verdict, verdict_label=verdict_label,
            rationale=rationale, recommended_actions=actions, evaluated_at=ctx.evaluated_at,
        )

    # EU001 適格国向け
    if country in _EU001_COUNTRIES:
        applicable_gea.append("EU001")
        rationale.append(f"仕向地 {country} は EU General Export Authorisation EU001 の適格国です。")

    # EU008 (暗号品目)
    if is_encryption and ctx.end_user_type == "commercial":
        applicable_gea.append("EU008")
        rationale.append("Category 5 Part 2 (情報セキュリティ) 品目: EU008（暗号品目の民間向け）が適用可能です。")

    # EU007 (グループ内技術移転)
    applicable_gea.append("EU007")
    rationale.append("グループ内の技術・ソフトウェア移転には EU007 が適用可能な場合があります。")

    if applicable_gea:
        verdict = "GEA"
        verdict_label = f"🔵 EU General Export Authorisation 利用可能 — {', '.join(applicable_gea)}"
        actions.append(f"GEA {applicable_gea[0]} の適用要件（Annex II）を確認してください。")
        actions.append("GEA 利用記録（Regulation Article 26: 5年間）を保存してください。")
        actions.append("一部加盟国は GEA 使用前の事前通知（notification）を要求する場合があります。")
    else:
        verdict = "INDIVIDUAL_LICENSE"
        verdict_label = "🔴 個別輸出許可が必要 — EU 加盟国所轄当局に申請"
        rationale.append("適用可能な EU General Export Authorisation がありません。個別許可申請が必要です。")
        actions.append("加盟国所轄当局（輸出元 EU 加盟国の経済省・輸出管理局）に Individual Export Licence を申請してください。")
        actions.append("Catch-all 規制（Art.4: WMD/Art.5: Military）の確認も並行して行ってください。")

    return EUDualUseJudgment(
        destination_country=country, eccn=ctx.eccn, eu_category=eu_cat,
        eu_category_label=eu_cat_label, is_eu_controlled=True, is_intra_eu=False,
        applicable_gea=applicable_gea,
        gea_labels=[GEA_LABELS.get(g, g) for g in applicable_gea],
        verdict=verdict, verdict_label=verdict_label,
        rationale=rationale, recommended_actions=actions, evaluated_at=ctx.evaluated_at,
    )
