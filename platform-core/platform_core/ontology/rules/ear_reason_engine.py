"""
platform_core.ontology.rules.ear_reason_engine
================================================
EAR (Export Administration Regulations) 規制理由・ライセンス例外エンジン。

根拠条文:
  - 15 CFR Part 742   — Control Policy; CCL-based Controls (Reasons for Control)
  - 15 CFR Part 740   — License Exceptions
  - 15 CFR Part 774   — Commerce Control List (CCL)
  - EAR Supplement No.1 to Part 738 (Country Chart)

判定フロー:
  1. ECCN カテゴリ・グループ から規制理由 (Reason for Control) を特定
  2. 仕向地 EAR Country Group から適用規制カラムを確認
  3. ライセンス例外 (License Exception) 適用可否を判定
  4. 総合判定 (NLR / License Exception / License Required)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── 規制理由ラベル ────────────────────────────────────────────────────────
REASON_LABELS: dict[str, str] = {
    "NS1":  "National Security (NS1) — 国家安全保障（Multilateral: Wassenaar）",
    "NS2":  "National Security (NS2) — 国家安全保障（Unilateral: 米国単独）",
    "AT1":  "Anti-Terrorism (AT1) — テロ対策（Multilateral: Wassenaar）",
    "AT2":  "Anti-Terrorism (AT2) — テロ対策（Unilateral: 米国単独）",
    "NP1":  "Non-Proliferation (NP1) — 核不拡散（Multilateral: NSG）",
    "NP2":  "Non-Proliferation (NP2) — 核不拡散（Unilateral: 米国単独）",
    "MT":   "Missile Technology (MT) — ミサイル技術（MTCR）",
    "CB1":  "Chemical & Biological (CB1) — 化学・生物（Multilateral: AG）",
    "CB2":  "Chemical & Biological (CB2) — 化学・生物（Unilateral）",
    "CB3":  "Chemical & Biological (CB3) — 化学・生物（CW条約関連）",
    "RS1":  "Regional Stability (RS1) — 地域安定性（Multilateral）",
    "RS2":  "Regional Stability (RS2) — 地域安定性（Unilateral）",
    "CC1":  "Crime Control (CC1) — 犯罪対策（Multilateral）",
    "CC2":  "Crime Control (CC2) — 犯罪対策（Unilateral）",
    "EI":   "Encryption Items (EI) — 暗号品目（§742.15）",
    "SS":   "Significant Items (SS) — 重要品目",
    "SL":   "Surreptitious Listening (SL) — 盗聴機器",
    "FC":   "Firearms Convention (FC) — 小火器条約",
}

# ── ECCN カテゴリ・グループ → 規制理由マッピング ──────────────────────────
# （CCL Part 774 各 ECCN の "Reasons for Control" 欄に基づく代表値）
_CATEGORY_REASONS: dict[str, list[str]] = {
    "0A": ["NS1", "NP1", "AT1"],          # Nuclear-related equipment/materials
    "0B": ["NS1", "NP1", "AT1"],
    "0C": ["NP1", "AT1"],
    "0D": ["NS1", "NP1", "AT1"],
    "0E": ["NS1", "NP1", "AT1"],
    "1A": ["CB1", "NP1", "AT1"],          # Special materials / chemicals
    "1B": ["CB1", "NP1", "MT", "AT1"],
    "1C": ["CB1", "CB2", "CB3", "NP1", "AT1"],
    "1D": ["CB1", "AT1"],
    "1E": ["CB1", "AT1"],
    "2A": ["NS1", "NP1", "AT1"],          # Materials processing
    "2B": ["NS1", "NP1", "MT", "AT1"],
    "2D": ["NS1", "NP1", "AT1"],
    "2E": ["NS1", "NP1", "AT1"],
    "3A": ["NS1", "AT1"],                  # Electronics
    "3B": ["NS1", "AT1"],
    "3C": ["NS1", "AT1"],
    "3D": ["NS1", "AT1"],
    "3E": ["NS1", "AT1"],
    "4A": ["NS1", "AT1"],                  # Computers
    "4D": ["NS1", "AT1"],
    "4E": ["NS1", "AT1"],
    "5A": ["NS1", "AT1"],                  # Telecom / Info Security (Part 1)
    "5B": ["NS1", "AT1"],
    "5D": ["NS1", "AT1"],
    "5E": ["NS1", "AT1"],
    "6A": ["NS1", "RS1", "AT1"],          # Sensors & Lasers
    "6B": ["NS1", "AT1"],
    "6C": ["NS1", "AT1"],
    "6D": ["NS1", "AT1"],
    "6E": ["NS1", "RS1", "AT1"],
    "7A": ["NS1", "MT", "AT1"],           # Navigation & Avionics
    "7B": ["NS1", "MT", "AT1"],
    "7D": ["NS1", "MT", "AT1"],
    "7E": ["NS1", "MT", "AT1"],
    "8A": ["NS1", "MT", "AT1"],           # Marine
    "8B": ["NS1", "MT", "AT1"],
    "8C": ["NS1", "AT1"],
    "8D": ["NS1", "AT1"],
    "8E": ["NS1", "MT", "AT1"],
    "9A": ["NS1", "MT", "AT1"],           # Aerospace & Propulsion
    "9B": ["NS1", "MT", "AT1"],
    "9C": ["NS1", "MT", "AT1"],
    "9D": ["NS1", "MT", "AT1"],
    "9E": ["NS1", "MT", "AT1"],
}

# 暗号品目 ECCN (EI 追加)
_ENCRYPTION_ECCNS = {"5A002", "5B002", "5D002", "5E002", "5A992", "5D992"}

# 化学/生物兵器製造設備 特定 ECCN
_CB_SPECIFIC = {"1C350", "1C351", "1C352", "1C353", "1C354", "2B350", "2B351", "2B352"}

# AT2 単独規制 ECCN (NS 非対象だが AT 対象)
_AT2_ECCNS_PREFIX = {"0", "1", "2"}  # EAR99 周辺でも AT2 が付く場合あり


# ── ライセンス例外 ──────────────────────────────────────────────────────────
LICENSE_EXCEPTION_LABELS: dict[str, str] = {
    "NLR": "No License Required (NLR) — 許可不要（EAR99 相当または Country Chart 未掲載）",
    "LVS": "Low Value Shipments (§740.2) — 少額取引（品目別価格制限あり）",
    "GBS": "Group B Countries (§740.4) — グループB諸国向け輸出",
    "CIV": "Civil End-Users (§740.6) — 民間最終利用者向け",
    "TSR": "Technology & Software — Restricted (§740.6) — 技術・ソフトウェア（制限付き）",
    "APP": "Computers (§740.7) — コンピュータ品目向け",
    "ENC": "Encryption (§740.17) — 暗号品目（一部 Mass-market 製品等）",
    "GOV": "Government End-Users (§740.11) — 政府・国際機関向け",
    "RPL": "Replacement Parts (§740.10) — 修理・交換部品",
    "STA": "Strategic Trade Authorization (§740.20) — 戦略的貿易許可（同盟国向け）",
}

# EAR Country Group A:1-A:6 (White Countries)
_COUNTRY_GROUP_A = {
    "AU", "AT", "BE", "BG", "CA", "HR", "CY", "CZ", "DK", "EE", "FI",
    "FR", "DE", "GR", "HU", "IS", "IE", "IT", "JP", "LV", "LI", "LT",
    "LU", "MT", "NL", "NZ", "NO", "PL", "PT", "RO", "SK", "SI", "ES",
    "SE", "CH", "TR", "GB", "US",
}

# Country Group D:1 (National Security concern countries - major)
_COUNTRY_GROUP_D1 = {
    "CN", "RU", "BY", "KP", "IR", "CU", "SY", "SD", "VE", "VN",
    "MM", "PK", "IN", "EG", "SA", "AE", "QA", "KW", "IL",
}

# Country Group E:1 (Embargoed - requires license almost always)
_COUNTRY_GROUP_E1 = {"KP", "IR", "CU", "SY", "SD"}

# STA 適格国 (A:5/A:6 同盟国 + 一部 B グループ)
_STA_ELIGIBLE = {
    "AU", "AT", "BE", "CA", "CZ", "DK", "FI", "FR", "DE", "GR", "HU",
    "IS", "IE", "IT", "JP", "LV", "LT", "LU", "NL", "NZ", "NO", "PL",
    "PT", "SK", "SI", "ES", "SE", "CH", "TR", "GB",
}


# ── データクラス ────────────────────────────────────────────────────────────
@dataclass
class EARContext:
    transaction_id:   int
    destination_country: str
    eccn:             Optional[str]
    end_user_type:    str = "unknown"   # commercial / government / military / unknown
    is_reexport:      bool = False
    evaluated_at:     str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class EARJudgment:
    transaction_id:        int
    destination_country:   str
    eccn:                  Optional[str]
    country_group:         str          # A / B / D:1 / E:1 / unknown
    reasons_for_control:   list[str]    # NS1/AT1/NP1 etc.
    reason_labels:         list[str]
    applicable_exceptions: list[str]    # NLR / LVS / STA etc.
    exception_labels:      list[str]
    verdict:               str          # NLR / EXCEPTION / LICENSE_REQUIRED / PROHIBITED
    verdict_label:         str
    rationale:             list[str]
    recommended_actions:   list[str]
    evaluated_at:          str

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_id":      self.transaction_id,
            "destination_country": self.destination_country,
            "eccn":                self.eccn,
            "country_group":       self.country_group,
            "reasons_for_control": self.reasons_for_control,
            "reason_labels":       self.reason_labels,
            "applicable_exceptions": self.applicable_exceptions,
            "exception_labels":    self.exception_labels,
            "verdict":             self.verdict,
            "verdict_label":       self.verdict_label,
            "rationale":           self.rationale,
            "recommended_actions": self.recommended_actions,
            "evaluated_at":        self.evaluated_at,
        }


def _get_country_group(country: str) -> str:
    c = country.upper()
    if c in _COUNTRY_GROUP_E1:
        return "E:1"
    if c in _COUNTRY_GROUP_D1:
        return "D:1"
    if c in _COUNTRY_GROUP_A:
        return "A"
    return "B"


def _get_reasons(eccn: Optional[str]) -> list[str]:
    if not eccn:
        return ["AT2"]
    e = eccn.upper().strip()
    reasons: list[str] = []

    # 暗号品目
    if e in _ENCRYPTION_ECCNS or e.startswith("5A002") or e.startswith("5D002") or e.startswith("5E002"):
        reasons.append("EI")

    # CB 特定品目
    if e in _CB_SPECIFIC:
        reasons = [r for r in reasons if r != "EI"]
        reasons = ["CB1", "CB2", "CB3"] + reasons

    # カテゴリ・グループ前2文字でマッチ
    prefix = e[:2] if len(e) >= 2 else ""
    cat_reasons = _CATEGORY_REASONS.get(prefix, [])
    for r in cat_reasons:
        if r not in reasons:
            reasons.append(r)

    # EAR99 / 不明: AT2 のみ
    if not reasons:
        reasons = ["AT2"]

    return reasons


def _get_applicable_exceptions(
    eccn: Optional[str],
    reasons: list[str],
    country_group: str,
    end_user_type: str,
) -> list[str]:
    exceptions: list[str] = []

    # E:1 → 例外なし（ほぼ全品目許可必要）
    if country_group == "E:1":
        return []

    # EAR99 相当（ECCN なし、規制理由 AT2 のみ）
    if not eccn or reasons == ["AT2"]:
        exceptions.append("NLR")
        return exceptions

    # グループ A 向け: NLR 適用範囲広い
    if country_group == "A":
        # STA はほぼ全品目で利用可能（A:5/A:6 諸国）
        exceptions.append("STA")
        # NS のみで CB/MT/NP がなければ NLR 可能性あり
        if not any(r in reasons for r in ["NP1", "NP2", "MT", "CB1", "CB2", "CB3"]):
            exceptions.append("NLR")
        return exceptions

    # 暗号品目 → ENC 例外
    if "EI" in reasons:
        exceptions.append("ENC")

    # D:1 向け
    if country_group == "D:1":
        # MT / NP / CB は例外なし（基本 License Required）
        if any(r in reasons for r in ["MT", "NP1", "NP2", "CB1", "CB2", "CB3"]):
            return exceptions  # ENC だけ残る
        # NS のみ → CIV（民間向け）が適用可能な場合あり
        if "NS1" in reasons and end_user_type == "commercial":
            exceptions.append("CIV")
        return exceptions

    # グループ B 向け
    exceptions.append("GBS")
    if "EI" not in reasons:
        exceptions.append("LVS")
    return exceptions


def evaluate_ear(ctx: EARContext) -> EARJudgment:
    """EAR Reason for Control + License Exception 総合判定。"""
    country = ctx.destination_country.upper().strip()
    cg = _get_country_group(country)
    reasons = _get_reasons(ctx.eccn)
    exceptions = _get_applicable_exceptions(ctx.eccn, reasons, cg, ctx.end_user_type)

    rationale: list[str] = []
    actions: list[str] = []

    # ── 判定ロジック ──
    if cg == "E:1":
        verdict = "PROHIBITED"
        verdict_label = "🚫 輸出禁止（E:1 禁輸国）— 原則として輸出許可は発行されません"
        rationale.append(f"仕向国 {country} は EAR Country Group E:1（禁輸国）に該当します。")
        rationale.append("EAR §746 に基づく包括的輸出制限が適用されます。")
        actions.append("本取引を即座に停止し、法務・コンプライアンス部門に報告してください。")
        actions.append("OFAC・BIS への必要な事前通知・届出手続きを確認してください。")

    elif not ctx.eccn or reasons == ["AT2"]:
        verdict = "NLR"
        verdict_label = "🟢 No License Required (NLR) — EAR99 相当品目"
        rationale.append("品目は EAR99 相当と判断されます（CCL 未掲載）。")
        rationale.append("E:1 禁輸国・制裁対象者への輸出でなければ許可不要です。")
        actions.append("Red Flag（懸念取引先・懸念用途）の有無を確認してください。")
        actions.append("キャッチオール規制（EAR Part 744）のチェックを行ってください。")

    elif "NLR" in exceptions or "STA" in exceptions:
        verdict = "NLR" if "NLR" in exceptions else "EXCEPTION"
        verdict_label = (
            "🟢 No License Required (NLR) — 許可不要"
            if verdict == "NLR"
            else "🔵 License Exception 適用可能"
        )
        applicable_exc = "NLR" if "NLR" in exceptions else (exceptions[0] if exceptions else "STA")
        rationale.append(f"仕向国 {country} は Country Group {cg} に該当します。")
        rationale.append(f"規制理由: {', '.join(reasons)}。ライセンス例外 {applicable_exc} が適用可能です。")
        if cg == "A" and "STA" in exceptions:
            rationale.append("STA (§740.20) により、同盟国への輸出は簡素化された手続きが可能です。")
        actions.append("取引先・エンドユーザーがデナイアル・オーダーまたは制裁リストに掲載されていないことを確認してください。")
        actions.append("輸出記録（EAR Part 762）を5年間保存してください。")

    elif exceptions:
        verdict = "EXCEPTION"
        verdict_label = f"🔵 License Exception 適用可能 — {', '.join(exceptions)}"
        rationale.append(f"仕向国 {country} は Country Group {cg} に該当します。")
        rationale.append(f"規制理由 {', '.join(reasons)} に対し、例外 {', '.join(exceptions)} の適用を検討してください。")
        actions.append(f"License Exception {exceptions[0]} の適用要件（§740 サブパート）を確認してください。")
        actions.append("License Exception 使用記録を維持し、BIS からの問い合わせに備えてください。")

    else:
        verdict = "LICENSE_REQUIRED"
        verdict_label = "🔴 License Required — EAR 輸出許可申請が必要"
        rationale.append(f"仕向国 {country} は Country Group {cg} に該当します。")
        rationale.append(f"規制理由 {', '.join(reasons)} に対し、適用可能な License Exception がありません。")
        if "MT" in reasons:
            rationale.append("MT (Missile Technology) 規制品目は、ほとんどの D:1 国向けで許可が必要です。")
        if any(r in reasons for r in ["NP1", "NP2"]):
            rationale.append("NP (Non-Proliferation) 規制品目は、核関連懸念国向けで許可が必要です。")
        if any(r in reasons for r in ["CB1", "CB2", "CB3"]):
            rationale.append("CB (Chemical & Biological) 規制品目は、WMD 懸念国向けで許可が必要です。")
        actions.append("BIS への輸出許可申請（Export License Application）を行ってください。")
        actions.append("申請先: https://www.bis.doc.gov/index.php/licensing/simplified-network-application-resubmission-system-snapr")
        actions.append("審査期間の目安: NS 品目 60 日 / 軍事関連品目 90〜180 日。")
        actions.append("許可取得まで輸出を停止してください。")

    return EARJudgment(
        transaction_id=ctx.transaction_id,
        destination_country=country,
        eccn=ctx.eccn,
        country_group=cg,
        reasons_for_control=reasons,
        reason_labels=[REASON_LABELS.get(r, r) for r in reasons],
        applicable_exceptions=exceptions,
        exception_labels=[LICENSE_EXCEPTION_LABELS.get(e, e) for e in exceptions],
        verdict=verdict,
        verdict_label=verdict_label,
        rationale=rationale,
        recommended_actions=actions,
        evaluated_at=ctx.evaluated_at,
    )
