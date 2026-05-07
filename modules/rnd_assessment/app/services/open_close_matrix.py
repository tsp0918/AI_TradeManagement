"""
open_close_matrix.py
=====================
オープンクローズ戦略マトリクス — 技術の知財戦略（特許出願 vs. 営業秘密）の判定支援。

根拠:
  - 経済安全保障推進法 第4章（特許非公開制度、2024年5月施行）
  - 外為法 第25条（技術移転規制）
  - 特許法 第36条（特許出願の公開義務）
  - 不正競争防止法 第2条第1項第4号〜10号（営業秘密）

判定マトリクス:
  ECCN 感度 × 特許非公開リスク × 競合状況 × 技術成熟度
  → OPEN（特許出願）/ CONDITIONAL（限定的特許）/ CLOSE（営業秘密）/ MANDATORY_CLOSE（非公開指定）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ── 高規制感度 ECCN カテゴリ（Cat 0/1/7/9 = MT/NP/CB 中心） ────────────────
_HIGH_SENSITIVITY_ECCN_PREFIXES = {
    "0",   # Nuclear
    "1C",  # Chemicals / biological materials
    "7A",  # Navigation / avionics (MT)
    "7D",  # Navigation software (MT)
    "7E",  # Navigation technology (MT)
    "9A",  # Aerospace engines (MT)
    "9D",  # Aerospace software (MT)
    "9E",  # Aerospace technology (MT)
}

_MEDIUM_SENSITIVITY_ECCN_PREFIXES = {
    "3A", "3D", "3E",  # Electronics (NS)
    "4A", "4D", "4E",  # Computers (NS)
    "5A", "5D", "5E",  # Telecom / Encryption (NS/EI)
    "6A", "6D", "6E",  # Sensors / Lasers (NS/RS)
    "8A", "8D", "8E",  # Marine (NS/MT)
    "2B",              # Materials processing (NP/NS)
}


def _eccn_sensitivity(eccn: Optional[str]) -> str:
    """ECCN から感度レベル (HIGH / MEDIUM / LOW) を返す。"""
    if not eccn:
        return "LOW"
    e = eccn.upper().strip()
    for prefix in _HIGH_SENSITIVITY_ECCN_PREFIXES:
        if e.startswith(prefix):
            return "HIGH"
    for prefix in _MEDIUM_SENSITIVITY_ECCN_PREFIXES:
        if e.startswith(prefix):
            return "MEDIUM"
    return "LOW"


@dataclass
class OpenCloseInput:
    title:                   str
    description:             str
    eccn:                    Optional[str]    = None
    patent_risk_level:       str             = "NONE"   # HIGH / MEDIUM / NONE
    tech_maturity:           str             = "middle"  # early / middle / mature
    competitive_position:    str             = "parity"  # ahead / parity / behind
    has_trade_secret_value:  bool            = True
    export_destination:      Optional[str]   = None     # ISO alpha-2 (target market)
    evaluated_at:            str             = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class OpenCloseRecommendation:
    strategy:            str    # OPEN / CONDITIONAL / CLOSE / MANDATORY_CLOSE
    strategy_label:      str
    strategy_color:      str    # CSS color hint
    eccn_sensitivity:    str
    patent_risk_level:   str
    rationale:           list[str]
    recommended_actions: list[str]
    ip_filing_guidance:  list[str]    # 出願・権利化の具体的指針
    law_references:      list[str]
    evaluated_at:        str

    def to_dict(self) -> dict:
        return {
            "strategy":            self.strategy,
            "strategy_label":      self.strategy_label,
            "strategy_color":      self.strategy_color,
            "eccn_sensitivity":    self.eccn_sensitivity,
            "patent_risk_level":   self.patent_risk_level,
            "rationale":           self.rationale,
            "recommended_actions": self.recommended_actions,
            "ip_filing_guidance":  self.ip_filing_guidance,
            "law_references":      self.law_references,
            "evaluated_at":        self.evaluated_at,
        }


def evaluate_open_close(inp: OpenCloseInput) -> OpenCloseRecommendation:
    """オープンクローズ戦略マトリクスを評価する。"""
    eccn_sens = _eccn_sensitivity(inp.eccn)
    rationale: list[str] = []
    actions: list[str] = []
    filing_guidance: list[str] = []
    law_refs: list[str] = []

    # ── MANDATORY_CLOSE: 経済安保法 高リスク → 特許出願不可 ─────────────────
    if inp.patent_risk_level == "HIGH":
        strategy = "MANDATORY_CLOSE"
        strategy_label = "⛔ 特許非公開（経済安保法）— 出願前に経済産業大臣への事前確認が必須"
        strategy_color = "#7f1d1d"
        rationale.append("経済安全保障推進法 第65条に基づく「特許非公開」指定の対象技術に該当する可能性があります。")
        rationale.append("本技術の特許出願は、出願前に内閣府・経済産業省への相談が法的に義務付けられています。")
        actions.append("【最優先】特許庁への出願前に、経済産業省 安全保障貿易管理課に相談してください。")
        actions.append("指定審査（第65条〜第70条）の対象となる場合、出願は保留・非公開処分となります。")
        actions.append("社内の知財部門・法務部門・輸出管理部門の3者で合同審議を行ってください。")
        filing_guidance.append("特許出願は原則として実施しないこと（「クローズ」戦略を採用）。")
        filing_guidance.append("核心技術は営業秘密として管理し、不正競争防止法による保護を活用してください。")
        filing_guidance.append("国内出願が許可される場合でも、PCT（国際出願）は特に慎重に検討してください。")
        law_refs.append("経済安全保障推進法 第65条（特定技術の非公開）")
        law_refs.append("経済安全保障推進法 第70条（違反した場合: 2年以下の懲役または100万円以下の罰金）")
        law_refs.append("不正競争防止法 第2条第1項第4〜10号（営業秘密保護）")
        return OpenCloseRecommendation(
            strategy=strategy, strategy_label=strategy_label, strategy_color=strategy_color,
            eccn_sensitivity=eccn_sens, patent_risk_level=inp.patent_risk_level,
            rationale=rationale, recommended_actions=actions, ip_filing_guidance=filing_guidance,
            law_references=law_refs, evaluated_at=inp.evaluated_at,
        )

    # ── CLOSE: 高感度 ECCN + 相談推奨レベルのリスク ─────────────────────────
    if eccn_sens == "HIGH" and inp.patent_risk_level in ("HIGH", "MEDIUM"):
        strategy = "CLOSE"
        strategy_label = "🔒 クローズ戦略（営業秘密）— 特許出願は慎重に検討"
        strategy_color = "#92400e"
        rationale.append(f"ECCN {inp.eccn or '不明'} は高規制感度（MT/NP/CB 関連）の技術カテゴリです。")
        rationale.append("特許出願により技術内容が公開されると、競合・懸念国による技術情報の入手を許す可能性があります。")
        if inp.patent_risk_level == "MEDIUM":
            rationale.append("経済安保法の事前確認対象技術（推奨レベル）にも該当する可能性があります。")
        actions.append("特許出願の前に、経済産業省 安全保障貿易管理課または特許庁への相談を推奨します。")
        actions.append("外為法 第25条（技術提供）の観点で、特許明細書の開示範囲を精査してください。")
        actions.append("競合がすでに類似技術を出願している場合のみ、防衛出願を検討してください。")
        filing_guidance.append("原則として営業秘密（Trade Secret）管理を選択することを推奨します。")
        filing_guidance.append("出願する場合でも、クレーム範囲を最小化し、核心ノウハウを明細書に記載しないよう設計してください。")
        filing_guidance.append("PCT 出願（国際公開）は特に回避し、国内優先権出願のみを検討してください。")
        law_refs.append("外為法 第25条（技術提供の許可）")
        law_refs.append("特許法 第36条（明細書の開示義務） — 出願すれば公開義務が生じる")
        law_refs.append("経済安全保障推進法 第65条（特定技術 10分野の非公開制度）")

    # ── CLOSE: 低成熟度 + 高い競争優位 ────────────────────────────────────
    elif inp.tech_maturity == "early" and inp.competitive_position == "ahead" and inp.has_trade_secret_value:
        strategy = "CLOSE"
        strategy_label = "🔒 クローズ戦略（営業秘密）— 技術的優位を特許公開で失わないため"
        strategy_color = "#92400e"
        rationale.append("技術が初期段階かつ競合に対して優位性がある場合、特許公開によって競合に模倣・改良の機会を与えるリスクがあります。")
        rationale.append("製造プロセス・ノウハウは特許で公開しても実質的保護が困難な場合が多く、営業秘密管理の方が有効です。")
        actions.append("秘密保持契約（NDA）と情報管理規程を整備し、営業秘密として管理してください。")
        actions.append("技術が市場で確立されてから特許出願を検討してください（CONDITIONAL 戦略への移行を視野に）。")
        filing_guidance.append("現時点での特許出願は推奨しません。技術成熟後に再評価してください。")
        filing_guidance.append("コア技術は営業秘密、量産技術・周辺技術は将来的な特許化を検討。")
        law_refs.append("不正競争防止法 第2条第1項第4〜10号（営業秘密 — 秘密管理性・有用性・非公知性の3要件）")

    # ── CONDITIONAL: 高感度 ECCN + 出願はするが限定的 ──────────────────────
    elif eccn_sens == "HIGH" and inp.patent_risk_level == "NONE":
        strategy = "CONDITIONAL"
        strategy_label = "⚠️ 条件付きオープン — クレーム範囲を限定した防衛出願を検討"
        strategy_color = "#78350f"
        rationale.append(f"ECCN {inp.eccn or '不明'} は高規制感度ですが、現時点で経済安保法の懸念技術には非該当です。")
        rationale.append("競合他社の類似出願を防ぐ「防衛出願」として限定的に特許取得を検討できます。")
        actions.append("クレームは製品・実施態様に絞り、製造ノウハウ・パラメータは明細書に記載しないよう設計してください。")
        actions.append("PCT 出願先の国を慎重に選定し、D:1/E:1 国を排除してください。")
        actions.append("外為法 第25条（技術提供）の観点で特許ライセンス先を制限する特約を検討してください。")
        filing_guidance.append("国内出願（日本特許庁）を優先し、PCT 出願は同盟国（A:1-A:6）向けに限定。")
        filing_guidance.append("明細書作成時に輸出管理部門のレビューを受けてください。")
        law_refs.append("外為法 第25条（技術提供規制）")
        law_refs.append("特許法 第36条（開示要件）")

    # ── CONDITIONAL: 中感度 ECCN + 競合追随 ────────────────────────────────
    elif eccn_sens == "MEDIUM" and inp.competitive_position in ("parity", "behind"):
        strategy = "CONDITIONAL"
        strategy_label = "⚠️ 条件付きオープン — 防衛出願または標準化戦略を検討"
        strategy_color = "#78350f"
        rationale.append("競合と技術レベルが拮抗しているため、特許出願による独占よりも、標準化・エコシステム戦略（オープン化）が有効な場合があります。")
        rationale.append("防衛出願により、競合の特許権による参入障壁を回避することが主目的となります。")
        actions.append("コア技術（差別化源泉）はクローズ（営業秘密）、周辺技術はオープン（特許公開）の組み合わせを検討。")
        actions.append("業界標準化団体（ETSI/ISO等）での標準化による FRAND ライセンスも検討してください。")
        filing_guidance.append("防衛出願を中心に、競合の包囲網を形成してください。")
        filing_guidance.append("コア製造プロセスは営業秘密、製品の外部仕様は特許として使い分ける。")
        law_refs.append("不正競争防止法（営業秘密管理）")
        law_refs.append("特許法 第36条（開示要件）")

    # ── OPEN: 低感度 + 競合優位 ────────────────────────────────────────────
    else:
        strategy = "OPEN"
        strategy_label = "🟢 オープン戦略（特許出願）— 積極的な権利化を推奨"
        strategy_color = "#14532d"
        rationale.append(f"ECCN感度が低く（{eccn_sens}）、特許出願による技術公開リスクは限定的です。")
        if inp.competitive_position == "ahead":
            rationale.append("競合に対して優位性があるため、特許による独占的地位の確立が有効です。")
        if inp.tech_maturity == "mature":
            rationale.append("技術が成熟しており、特許出願による保護が事業競争力に直結します。")
        actions.append("特許出願を早期に実施し、先願主義（日本・EU・US）の優先権を確保してください。")
        actions.append("PCT 出願（国際特許出願）により、主要市場（US/EU/CN/JP/KR）での権利保護を検討。")
        actions.append("競合他社の特許動向を定期的にモニタリングし、FTO（Freedom to Operate）分析を行ってください。")
        filing_guidance.append("国内優先権出願 → PCT 出願（12ヶ月以内）の標準ルートを推奨。")
        filing_guidance.append("クレームは広くドラフトし、将来の実施態様を包括的にカバーしてください。")
        filing_guidance.append("競合出願の無効化（IPR/post-grant review）を見据えた明細書の充実を図ってください。")
        law_refs.append("特許法 第36条（明細書要件）")
        law_refs.append("特許協力条約（PCT）— 国際出願")

    if not law_refs:
        law_refs.append("外為法 第25条（技術提供規制）")
        law_refs.append("不正競争防止法 第2条第1項第4〜10号（営業秘密）")

    return OpenCloseRecommendation(
        strategy=strategy, strategy_label=strategy_label, strategy_color=strategy_color,
        eccn_sensitivity=eccn_sens, patent_risk_level=inp.patent_risk_level,
        rationale=rationale, recommended_actions=actions, ip_filing_guidance=filing_guidance,
        law_references=law_refs, evaluated_at=inp.evaluated_at,
    )
