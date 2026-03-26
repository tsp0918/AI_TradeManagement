"""
reexport_control.py
────────────────────────────────────────────────────────────────────────────
EAR Country Chart × ECCN による再輸出規制アセスメントサービス。

ロジック:
  1. 品目の ECCN を取得
  2. ECCN の Reason for Control (RFC) コードを ccl_eccn_entries_v8.json から引く
  3. BIS EAR Country Chart (15 CFR Part 738 Supp. No.1) で
     宛先国 × RFC → "X"（要許可）/ 空欄（NLR）を判定
  4. EAR99 / AT1 only / 要許可 を verdict として返す

Country Chart 収録国:
  JP（日本）、US（米国起点）、CN（中国）、EU（欧州連合、DE代表）、KR（韓国）

重要注意:
  - 本アセスメントは IT システムによる自動スクリーニングであり、
    法的助言ではありません。最終判断は必ず輸出管理担当者・法務の確認を要します。
  - EAR Country Chart は BIS が随時更新します。定期的な確認を推奨します。
    https://www.bis.doc.gov/index.php/documents/regulations-docs/2255-supplement-no-1-to-part-738-commerce-country-chart/file

RFC コード:
  NS  National Security
  MT  Missile Technology
  NP  Nuclear Nonproliferation
  CB  Chemical & Biological Weapons
  AT  Anti-Terrorism
  FC  Firearms Convention
  RS  Regional Stability
  CC  Crime Control
  SL  Surreptitious Listening
  SS  Short Supply
  EI  Encryption Items
  UN  United Nations Embargo
  SI  Significant Items
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_ECCN_JSON = Path(__file__).resolve().parents[4] / "data" / "staging" / "ccl_eccn_entries_v8.json"


# ── EAR Country Chart（簡略版）──────────────────────────────────────────────
# 出典: 15 CFR Part 738, Supplement No. 1 (BIS, 2024 edition)
# "X" = License Required, "" = No License Required (NLR)
# 列: NS1 NS2 MT1 NP1 NP2 CB1 CB2 CB3 AT1 FC1 RS1 RS2 CC1 CC2 SL UN SI
# ※ US は輸出元として扱うため Country Chart 対象外。"origin" と記載。

_CHART_COLUMNS = ["NS1","NS2","MT1","NP1","NP2","CB1","CB2","CB3","AT1","FC1","RS1","RS2","CC1","CC2","SL","UN","SI"]

# Country → column → "X" or ""
_COUNTRY_CHART: dict[str, dict[str, str]] = {
    "JP": {   # Japan — Country Group A:1, A:5, B
        "NS1": "",  "NS2": "",  "MT1": "",
        "NP1": "X", "NP2": "",
        "CB1": "",  "CB2": "",  "CB3": "",
        "AT1": "X",
        "FC1": "",
        "RS1": "",  "RS2": "",
        "CC1": "",  "CC2": "",
        "SL":  "",  "UN":  "",  "SI":  "",
    },
    "US": {   # United States — 輸出元（EAR の適用対象外として扱う）
        "NS1": "",  "NS2": "",  "MT1": "",
        "NP1": "X", "NP2": "",
        "CB1": "",  "CB2": "",  "CB3": "",
        "AT1": "",
        "FC1": "",
        "RS1": "",  "RS2": "",
        "CC1": "",  "CC2": "",
        "SL":  "",  "UN":  "",  "SI":  "",
    },
    "CN": {   # China (PRC) — Country Group D:1, D:2, D:3, D:4, D:5, E:1
        "NS1": "X", "NS2": "X", "MT1": "X",
        "NP1": "X", "NP2": "X",
        "CB1": "X", "CB2": "X", "CB3": "X",
        "AT1": "X",
        "FC1": "X",
        "RS1": "X", "RS2": "X",
        "CC1": "X", "CC2": "X",
        "SL":  "X", "UN":  "X", "SI":  "X",
    },
    "EU": {   # EU (Germany 代表) — Country Group A:1, A:5, B
        "NS1": "",  "NS2": "",  "MT1": "",
        "NP1": "X", "NP2": "",
        "CB1": "",  "CB2": "",  "CB3": "",
        "AT1": "X",
        "FC1": "",
        "RS1": "",  "RS2": "",
        "CC1": "",  "CC2": "",
        "SL":  "",  "UN":  "",  "SI":  "",
    },
    "KR": {   # Korea (South) — Country Group A:1, B
        "NS1": "",  "NS2": "",  "MT1": "",
        "NP1": "X", "NP2": "",
        "CB1": "",  "CB2": "",  "CB3": "",
        "AT1": "X",
        "FC1": "",
        "RS1": "",  "RS2": "",
        "CC1": "",  "CC2": "",
        "SL":  "",  "UN":  "",  "SI":  "",
    },
}

# RFC 高位コード → 使用するカラム（デフォルト: Column 1 / 最も広い規制を適用）
# ※ EAR の正式な判定は ECCN の license_requirements テキストに記載のカラム番号を参照
_RFC_TO_COLUMNS: dict[str, list[str]] = {
    "NS":  ["NS1"],
    "MT":  ["MT1"],
    "NP":  ["NP1"],
    "CB":  ["CB1"],
    "AT":  ["AT1"],
    "FC":  ["FC1"],
    "RS":  ["RS1"],
    "CC":  ["CC1"],
    "SL":  ["SL"],
    "SS":  [],         # Short Supply — commodity-specific, 手動確認要
    "EI":  [],         # Encryption — ENC exception で多くは対応可
    "UN":  ["UN"],
    "SI":  ["SI"],
}

# 特殊フラグ: これらの RFC は外部規制・軍事管理に関係し要注意
_HIGH_RISK_RFC = {"MT", "NP", "CB", "SL", "UN", "SI"}


# ── ECCN ルックアップ ──────────────────────────────────────────────────────────

_eccn_index: dict[str, dict] | None = None


def _load_eccn_index() -> dict[str, dict]:
    global _eccn_index
    if _eccn_index is not None:
        return _eccn_index
    if not _ECCN_JSON.exists():
        _eccn_index = {}
        return _eccn_index
    with open(_ECCN_JSON, encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("entries", data) if isinstance(data, dict) else data
    _eccn_index = {e["eccn"]: e for e in entries if e.get("eccn")}
    return _eccn_index


def get_rfc_codes(eccn: str) -> list[str]:
    """ECCN の Reason for Control コードリストを返す。"""
    idx = _load_eccn_index()
    entry = idx.get(eccn.upper().strip())
    if not entry:
        return []
    rfc = entry.get("reason_for_control", [])
    if isinstance(rfc, str):
        return [r.strip() for r in rfc.split(",") if r.strip()]
    return list(rfc)


# ── アセスメントコア ──────────────────────────────────────────────────────────

@dataclass
class ControlItem:
    """個別の規制理由と判定結果。"""
    rfc:        str            # NS / MT / NP / CB / AT / ...
    column:     str            # NS1 / MT1 / ...
    required:   bool           # True = License Required
    high_risk:  bool           # True = 要注意カテゴリ


@dataclass
class ReexportAssessment:
    """再輸出規制アセスメント結果。"""
    eccn:            str
    destination:     str
    verdict:         str       # "no_license_required" / "at1_only" / "license_required" / "ear99" / "unknown"
    verdict_label:   str       # 表示用日本語
    verdict_color:   str       # green / yellow / red / gray
    controls:        list[ControlItem] = field(default_factory=list)
    notes:           list[str]         = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "eccn":           self.eccn,
            "destination":    self.destination,
            "verdict":        self.verdict,
            "verdict_label":  self.verdict_label,
            "verdict_color":  self.verdict_color,
            "controls": [
                {
                    "rfc":      c.rfc,
                    "column":   c.column,
                    "required": c.required,
                    "high_risk":c.high_risk,
                }
                for c in self.controls
            ],
            "notes": self.notes,
        }


def assess_reexport(
    eccn:        str,
    destination: str,
    origin:      Optional[str] = None,
) -> ReexportAssessment:
    """ECCN × 仕向国 の再輸出規制を判定する。

    Args:
        eccn:        品目の ECCN コード（例: "3A001"）
        destination: 仕向国コード（"JP" / "US" / "CN" / "EU" / "KR"）
        origin:      仕出国コード（現在はログ用のみ）

    Returns:
        ReexportAssessment
    """
    eccn_clean = (eccn or "").upper().strip()
    dest_upper = (destination or "").upper()

    # EAR99 判定
    if eccn_clean in ("EAR99", "") or not eccn_clean:
        return ReexportAssessment(
            eccn=eccn_clean or "EAR99",
            destination=dest_upper,
            verdict="ear99",
            verdict_label="EAR99（規制対象外）",
            verdict_color="green",
            notes=["EAR99 品目は Country Chart 不適用。通常の輸出要件のみ確認してください。"],
        )

    # Country Chart 未対応国
    chart = _COUNTRY_CHART.get(dest_upper)
    if chart is None:
        return ReexportAssessment(
            eccn=eccn_clean,
            destination=dest_upper,
            verdict="unknown",
            verdict_label="判定不可（未対応国）",
            verdict_color="gray",
            notes=[f"Country Chart データは JP/US/CN/EU/KR のみ対応しています。"],
        )

    # RFC 取得
    rfcs = get_rfc_codes(eccn_clean)
    if not rfcs:
        # CCL に ECCN はあるが RFC が空 → 管理品目だが広範な許可不要の可能性
        return ReexportAssessment(
            eccn=eccn_clean,
            destination=dest_upper,
            verdict="no_license_required",
            verdict_label="NLR（要確認）",
            verdict_color="yellow",
            notes=[
                f"ECCN {eccn_clean} の Reason for Control が取得できませんでした。",
                "BIS の公式 CCL で license_requirements を直接確認してください。",
                "https://www.bis.doc.gov/index.php/regulations/commerce-control-list-ccl",
            ],
        )

    # Country Chart × RFC で判定
    controls: list[ControlItem] = []
    license_required_rfcs: list[str] = []

    for rfc in rfcs:
        columns = _RFC_TO_COLUMNS.get(rfc, [])
        if not columns:
            # SS / EI など — 手動確認
            controls.append(ControlItem(
                rfc=rfc, column="(see EAR)", required=False, high_risk=(rfc in _HIGH_RISK_RFC)
            ))
            continue
        for col in columns:
            required = chart.get(col, "") == "X"
            controls.append(ControlItem(
                rfc=rfc, column=col, required=required, high_risk=(rfc in _HIGH_RISK_RFC)
            ))
            if required:
                license_required_rfcs.append(f"{rfc}/{col}")

    # Verdict 決定
    notes: list[str] = []
    if license_required_rfcs:
        # AT1 のみかどうか確認
        non_at_required = [c for c in controls if c.required and c.rfc != "AT"]
        if not non_at_required and any(c.rfc == "AT" and c.required for c in controls):
            verdict       = "at1_only"
            verdict_label = "AT1のみ要許可"
            verdict_color = "yellow"
            notes.append("AT Column 1 のみ該当。License Exception ATF/SCP が利用できる場合があります。")
        else:
            verdict       = "license_required"
            verdict_label = "要許可（License Required）"
            verdict_color = "red"
            notes.append(f"以下の規制理由で輸出許可申請が必要です: {', '.join(license_required_rfcs)}")
    else:
        verdict       = "no_license_required"
        verdict_label = "NLR（許可不要）"
        verdict_color = "green"
        notes.append("Commerce Country Chart では許可不要です（NLR）。ただしエンドユーザー確認等は別途必要です。")

    # 高リスク RFC 注意
    high_risk_hits = [c.rfc for c in controls if c.high_risk]
    if high_risk_hits:
        notes.append(f"注意: 高リスク規制理由が含まれています: {', '.join(sorted(set(high_risk_hits)))}")

    # 中国向け特別注意
    if dest_upper == "CN":
        notes.append("中国向け: Entity List / Military End User (MEU) チェックも必須です。")

    notes.append("本判定は自動スクリーニングです。輸出管理担当者による最終確認を実施してください。")

    return ReexportAssessment(
        eccn=eccn_clean,
        destination=dest_upper,
        verdict=verdict,
        verdict_label=verdict_label,
        verdict_color=verdict_color,
        controls=controls,
        notes=notes,
    )
