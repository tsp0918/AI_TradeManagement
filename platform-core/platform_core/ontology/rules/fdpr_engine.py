"""
platform_core.ontology.rules.fdpr_engine
==========================================
FDPR (Foreign Direct Product Rule) 判定エンジン。

根拠条文:
  - 15 CFR §734.9  — Foreign Direct Product Rules (各 FDPR バリアント)
  - 15 CFR §736.2(b)(3) — General FDPR reexport/export prohibition
  - 15 CFR §734.4  — De minimis rule
  - EAR Supplement 1 to Part 740 (Country Groups)

判定フロー:
  1. 仕向地の EAR Country Group / 禁輸国チェック
  2. De Minimis 例外の適用可否（米国原産コンテンツ比率 vs 閾値）
  3. 適用 FDPR バリアントの決定:
       • Russia/Belarus FDPR (§734.9(f)) — D:1∩D:5 向け
       • China MEU FDPR (§734.9(e))     — D:1∩D:5 向け CN
       • Advanced Computing FDPR (§734.9(h)) — 先端半導体
       • General FDPR (§734.9(b/c/d))   — NS1/MT1 品目
  4. 総合判定
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── EAR Country Group マッピング（既存 catchall_engine が読む JSON を共有）──────
_CONCERN_COUNTRIES_PATH = (
    __import__("pathlib").Path(__file__).parent.parent / "seed" / "catchall_concern_countries.json"
)


@lru_cache(maxsize=1)
def _load_country_index() -> dict[str, dict[str, Any]]:
    import json
    try:
        raw = json.loads(_CONCERN_COUNTRIES_PATH.read_text(encoding="utf-8"))
        return {e["iso_code"]: e for e in raw}
    except Exception as exc:
        logger.warning("FDPR: 国データ読み込み失敗: %s", exc)
        return {}


# ── FDPR バリアント定義 ──────────────────────────────────────────────────────────

class FDPRVariant:
    RUSSIA_BELARUS = "russia_belarus_fdpr"       # §734.9(f)
    CHINA_MEU      = "china_meu_fdpr"            # §734.9(e)
    ADVANCED_COMP  = "advanced_computing_fdpr"   # §734.9(h) — Oct 2022
    GENERAL        = "general_fdpr"              # §734.9(b/c) — NS1/MT1


# De minimis 閾値 (%)
_DE_MINIMIS_THRESHOLD: dict[str, float] = {
    "embargoed_e1":    0.0,   # E:1 禁輸国（イラン・シリア・キューバ・北朝鮮）
    "russia_belarus": 0.0,   # Russia/Belarus FDPR（閾値なし）
    "d1_general":     10.0,  # 一般 D:1 国（10%ルール）
    "default":        25.0,  # 上記以外（EAR §734.4 一般）
}

# ECCN 制御理由: NS/MT/CB/NP は FDPR トリガー対象
_FDPR_TRIGGER_ECCN_PREFIXES = ("3A", "3B", "3C", "3D", "3E",  # 電子機器
                                "4A", "4D", "4E",               # コンピュータ
                                "5A", "5D", "5E",               # 通信/暗号
                                "6A", "6D", "6E",               # センサー
                                "7A", "7D", "7E",               # 航法
                                "8A", "8C", "8D", "8E",         # 海洋
                                "9A", "9B", "9D", "9E",         # 宇宙/推進
                                "1C",                            # 材料（化学）
                                "2B",)                           # 製造設備

# Advanced Computing FDPR: A100/H100 相当以上のチップ
_ADVANCED_COMPUTING_ECCNS = ("3A090", "3A991", "4A090", "4A994")


# ── データクラス ─────────────────────────────────────────────────────────────────

@dataclass
class FDPRContext:
    """FDPR 判定に必要な入力情報。"""
    transaction_id: int
    destination_country: str          # ISO alpha-2

    # 品目情報
    eccn: Optional[str] = None        # 判定品目の ECCN（例: "3A090"）
    item_description: str = ""

    # 米国原産コンテンツ
    us_content_pct: float = 0.0       # 米国原産コンテンツの価値比率（%）
    manufactured_using_us_tech: bool = False  # 米国管理技術を使用して製造
    us_tech_eccns: list[str] = field(default_factory=list)  # 使用した米国技術の ECCN

    # エンドユーザー
    end_user_type: str = "unknown"    # "commercial" | "military" | "government" | "unknown"
    is_reexport: bool = False

    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class FDPRReason:
    code: str
    label: str
    detail: str


@dataclass
class FDPRJudgment:
    """FDPR 判定結果。"""
    transaction_id: int
    evaluated_at: datetime

    # 仕向地情報
    destination_country: str
    destination_name: Optional[str]
    ear_groups: list[str]
    is_embargoed: bool

    # 適用バリアント
    applicable_variants: list[str]

    # De Minimis
    de_minimis_threshold: float
    de_minimis_applicable: bool      # 例外が適用されるか

    # 総合判定
    verdict: str                     # "us_license_required" | "review_recommended" | "not_applicable" | "indeterminate"
    verdict_label: str
    verdict_detail: str
    reasons: list[FDPRReason]
    recommended_actions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_id":       self.transaction_id,
            "evaluated_at":         self.evaluated_at.isoformat(),
            "destination_country":  self.destination_country,
            "destination_name":     self.destination_name,
            "ear_groups":           self.ear_groups,
            "is_embargoed":         self.is_embargoed,
            "applicable_variants":  self.applicable_variants,
            "de_minimis_threshold": self.de_minimis_threshold,
            "de_minimis_applicable": self.de_minimis_applicable,
            "verdict":              self.verdict,
            "verdict_label":        self.verdict_label,
            "verdict_detail":       self.verdict_detail,
            "reasons":              [{"code": r.code, "label": r.label, "detail": r.detail}
                                     for r in self.reasons],
            "recommended_actions":  self.recommended_actions,
        }


# ── メイン評価関数 ───────────────────────────────────────────────────────────────

def evaluate_fdpr(ctx: FDPRContext) -> FDPRJudgment:
    """
    FDPR 判定を実行し FDPRJudgment を返す。

    Args:
        ctx: FDPRContext — 品目・仕向地・米国コンテンツ情報

    Returns:
        FDPRJudgment — 適用バリアント・De Minimis 判定・総合 verdict
    """
    reasons: list[FDPRReason] = []
    applicable_variants: list[str] = []

    # ── Step 1: 仕向地情報取得 ────────────────────────────────────────────────────
    country_idx = _load_country_index()
    country_info = country_idx.get((ctx.destination_country or "").upper())

    dest_name: Optional[str] = None
    ear_groups: list[str] = []
    is_embargoed = False
    is_d1 = False
    is_d5 = False

    if country_info:
        dest_name    = country_info.get("name_ja")
        ear_groups   = country_info.get("ear_groups", [])
        is_embargoed = country_info.get("ear_is_embargoed", False)
        is_d1        = "D:1" in ear_groups
        is_d5        = "D:5" in ear_groups

    dest = ctx.destination_country.upper() if ctx.destination_country else "??"

    # ── Step 2: 製造情報チェック（FDPR 発動条件：米国技術使用）─────────────────
    if not ctx.manufactured_using_us_tech and ctx.us_content_pct == 0.0:
        return FDPRJudgment(
            transaction_id=ctx.transaction_id,
            evaluated_at=ctx.evaluated_at,
            destination_country=dest,
            destination_name=dest_name,
            ear_groups=ear_groups,
            is_embargoed=is_embargoed,
            applicable_variants=[],
            de_minimis_threshold=_DE_MINIMIS_THRESHOLD["default"],
            de_minimis_applicable=True,
            verdict="not_applicable",
            verdict_label="FDPR 非適用（米国原産コンテンツなし）",
            verdict_detail=(
                "米国管理技術・ソフトウェアを使用して製造されていないため、"
                "FDPR（外国直接品規則）の適用対象外です。"
            ),
            reasons=[FDPRReason(
                code="NO_US_CONTENT",
                label="米国原産コンテンツなし",
                detail="製品に米国管理技術・ソフトウェアが含まれていないと申告されています。"
            )],
            recommended_actions=["米国原産コンテンツの有無を定期的に再確認してください。"],
        )

    reasons.append(FDPRReason(
        code="US_CONTENT_DETECTED",
        label=f"米国原産コンテンツ: {ctx.us_content_pct:.1f}%",
        detail=(
            f"製品の {ctx.us_content_pct:.1f}% が米国管理技術・ソフトウェア由来です。"
            + (f" 使用技術 ECCN: {', '.join(ctx.us_tech_eccns)}" if ctx.us_tech_eccns else "")
        ),
    ))

    # ── Step 3: De Minimis 閾値決定 ───────────────────────────────────────────────
    if is_embargoed:
        dm_threshold = _DE_MINIMIS_THRESHOLD["embargoed_e1"]
    elif dest in ("RU", "BY"):
        dm_threshold = _DE_MINIMIS_THRESHOLD["russia_belarus"]
    elif is_d1:
        dm_threshold = _DE_MINIMIS_THRESHOLD["d1_general"]
    else:
        dm_threshold = _DE_MINIMIS_THRESHOLD["default"]

    de_minimis_applicable = (
        dm_threshold > 0 and ctx.us_content_pct < dm_threshold
    )

    if de_minimis_applicable:
        reasons.append(FDPRReason(
            code="DE_MINIMIS_EXCEPTION",
            label=f"De Minimis 例外適用（{ctx.us_content_pct:.1f}% < {dm_threshold:.0f}%）",
            detail=(
                f"EAR §734.4 De Minimis ルールにより、米国原産コンテンツ比率 {ctx.us_content_pct:.1f}% が"
                f"閾値 {dm_threshold:.0f}% を下回るため、FDPR の適用が除外されます。"
            ),
        ))
    else:
        if dm_threshold == 0:
            reasons.append(FDPRReason(
                code="NO_DE_MINIMIS",
                label="De Minimis 例外なし（禁輸国または Russia/Belarus FDPR）",
                detail=(
                    f"仕向地「{dest_name or dest}」は De Minimis 例外が適用されない国です。"
                    "米国原産コンテンツが 1% でも含まれる場合は FDPR が適用されます。"
                ),
            ))
        else:
            reasons.append(FDPRReason(
                code="DE_MINIMIS_EXCEEDED",
                label=f"De Minimis 超過（{ctx.us_content_pct:.1f}% ≥ {dm_threshold:.0f}%）",
                detail=(
                    f"米国原産コンテンツ比率 {ctx.us_content_pct:.1f}% が"
                    f"De Minimis 閾値 {dm_threshold:.0f}% 以上のため例外は適用されません。"
                ),
            ))

    # ── Step 4: FDPR バリアント判定 ───────────────────────────────────────────────

    # Russia/Belarus FDPR (§734.9(f))
    if dest in ("RU", "BY") or (is_d1 and is_d5 and dest in ("RU", "BY")):
        applicable_variants.append(FDPRVariant.RUSSIA_BELARUS)
        reasons.append(FDPRReason(
            code="RUSSIA_BELARUS_FDPR",
            label=f"Russia/Belarus FDPR 適用 (EAR §734.9(f))",
            detail=(
                f"仕向地「{dest_name or dest}」は Russia/Belarus FDPR の対象です。"
                "2022年2月以降、実質的にすべての米国原産コンテンツを含む品目に BIS 許可が必要です。"
            ),
        ))

    # China MEU FDPR (§734.9(e))
    if dest == "CN" and ctx.end_user_type in ("military", "government"):
        applicable_variants.append(FDPRVariant.CHINA_MEU)
        reasons.append(FDPRReason(
            code="CHINA_MEU_FDPR",
            label="China Military End User FDPR 適用 (EAR §734.9(e))",
            detail=(
                "中国向け軍/政府エンドユーザーへの輸出に China MEU FDPR が適用されます。"
                "NS/MT 制御品目は BIS 許可が必要です。"
            ),
        ))

    # Advanced Computing FDPR (§734.9(h))
    eccn_upper = (ctx.eccn or "").upper()
    if any(eccn_upper.startswith(ac) for ac in _ADVANCED_COMPUTING_ECCNS):
        if dest in ("CN", "RU", "BY") or is_embargoed:
            applicable_variants.append(FDPRVariant.ADVANCED_COMP)
            reasons.append(FDPRReason(
                code="ADVANCED_COMPUTING_FDPR",
                label=f"Advanced Computing FDPR 適用 (EAR §734.9(h)) — ECCN: {ctx.eccn}",
                detail=(
                    f"ECCN {ctx.eccn} は Advanced Computing FDPR の対象品目です。"
                    "A100/H100 相当以上の演算性能を持つ半導体は 2022年10月以降規制強化されています。"
                ),
            ))

    # General FDPR (§734.9(b/c))
    is_fdpr_eccn = any(eccn_upper.startswith(p) for p in _FDPR_TRIGGER_ECCN_PREFIXES)
    if is_fdpr_eccn and is_d1 and FDPRVariant.RUSSIA_BELARUS not in applicable_variants:
        applicable_variants.append(FDPRVariant.GENERAL)
        reasons.append(FDPRReason(
            code="GENERAL_FDPR",
            label=f"General FDPR 適用 (EAR §734.9(b)) — ECCN: {ctx.eccn}",
            detail=(
                f"ECCN {ctx.eccn} は NS/MT 制御対象品目であり、"
                f"仕向地「{dest_name or dest}」(D:1) への再輸出に General FDPR が適用されます。"
            ),
        ))

    # ── Step 5: 総合判定 ──────────────────────────────────────────────────────────
    if not applicable_variants:
        verdict = "not_applicable"
        verdict_label = "FDPR 非適用"
        verdict_detail = (
            "適用される FDPR バリアントが確認されませんでした。"
            "ただし、品目仕様・エンドユーザー情報の変化により状況が変わる可能性があります。"
        )
        actions = ["品目・仕向地情報に変化があれば再判定を実施してください。"]

    elif de_minimis_applicable and FDPRVariant.RUSSIA_BELARUS not in applicable_variants:
        verdict = "not_applicable"
        verdict_label = "FDPR 非適用（De Minimis 例外）"
        verdict_detail = (
            f"FDPR バリアント ({', '.join(applicable_variants)}) が確認されましたが、"
            f"De Minimis 例外（米国原産 {ctx.us_content_pct:.1f}% < {dm_threshold:.0f}%）により除外されます。"
        )
        actions = [
            "米国原産コンテンツ比率を継続的に管理してください。",
            "比率が閾値を超えた場合は即座に再判定・輸出管理部門へ報告してください。",
        ]

    elif applicable_variants:
        verdict = "us_license_required"
        variant_labels = {
            FDPRVariant.RUSSIA_BELARUS: "Russia/Belarus FDPR",
            FDPRVariant.CHINA_MEU:      "China MEU FDPR",
            FDPRVariant.ADVANCED_COMP:  "Advanced Computing FDPR",
            FDPRVariant.GENERAL:        "General FDPR",
        }
        vnames = [variant_labels.get(v, v) for v in applicable_variants]
        verdict_label = f"米国 BIS 許可申請 必須（{' / '.join(vnames)}）"
        verdict_detail = (
            f"以下の FDPR バリアントが適用されるため、米国 BIS への輸出許可申請が必要です: "
            f"{', '.join(vnames)}。"
            "De Minimis 例外も適用されません。"
        )
        actions = [
            "輸出管理部門・法務部門へ即座に報告してください。",
            "BIS への輸出許可申請（EAR Form BIS-748P 相当）を準備してください。",
            "許可取得前に輸出・技術提供を行わないでください。",
            "米国の取引先（技術供与元）への通知・確認を実施してください。",
            "外為法上の役務取引許可（第25条）との重複確認も実施してください。",
        ]

    else:
        verdict = "review_recommended"
        verdict_label = "要確認（情報不足）"
        verdict_detail = "ECCN・米国原産コンテンツ情報が不完全なため判定できません。"
        actions = ["ECCN および米国原産コンテンツ比率を確認して再判定してください。"]

    return FDPRJudgment(
        transaction_id=ctx.transaction_id,
        evaluated_at=ctx.evaluated_at,
        destination_country=dest,
        destination_name=dest_name,
        ear_groups=ear_groups,
        is_embargoed=is_embargoed,
        applicable_variants=applicable_variants,
        de_minimis_threshold=dm_threshold,
        de_minimis_applicable=de_minimis_applicable,
        verdict=verdict,
        verdict_label=verdict_label,
        verdict_detail=verdict_detail,
        reasons=reasons,
        recommended_actions=actions,
    )
