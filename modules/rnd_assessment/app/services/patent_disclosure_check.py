"""
経済安全保障推進法 第4章「特許出願の非公開」対応チェッカー。
2024年5月施行。Cabinet Order No.69 (2022) Art.22 の特定技術分野に
該当するR&Dプロジェクトは特許出願前に内閣府への事前確認が必要となる
可能性がある。違反した場合は2年以下の懲役または100万円以下の罰金。
"""
from __future__ import annotations

from dataclasses import dataclass, field

_SPECIFIED_TECH_CATEGORIES: list[dict] = [
    {
        "id": "weapons",
        "label": "武器・火薬類関連技術",
        "article": "第22条第1号",
        "keywords": [
            "武器", "銃器", "爆発物", "火薬", "弾薬", "誘導弾", "ミサイル",
            "爆弾", "地雷", "魚雷", "起爆", "信管",
            "weapon", "explosive", "ordnance", "munition", "missile",
            "projectile", "warhead", "detonator", "fuze",
        ],
        "risk_level": "HIGH",
    },
    {
        "id": "aircraft_defense",
        "label": "航空機・防衛関連技術",
        "article": "第22条第2号",
        "keywords": [
            "軍用機", "戦闘機", "無人機", "ステルス", "赤外線センサ",
            "レーダー吸収", "対空", "電子妨害", "電子戦",
            "military aircraft", "fighter", "stealth", "avionics",
            "radar absorbing", "electronic warfare", "countermeasure",
        ],
        "risk_level": "HIGH",
    },
    {
        "id": "space",
        "label": "宇宙開発・衛星・推進技術",
        "article": "第22条第3号",
        "keywords": [
            "人工衛星", "ロケット", "宇宙", "打ち上げ", "軌道", "推進剤",
            "スクラムジェット", "極超音速", "パルスデトネーション",
            "satellite", "rocket", "launch vehicle", "propellant",
            "orbital", "scramjet", "hypersonic", "pulse detonation",
        ],
        "risk_level": "HIGH",
    },
    {
        "id": "nuclear",
        "label": "原子力・核関連技術",
        "article": "第22条第4号",
        "keywords": [
            "原子炉", "核分裂", "核融合", "ウラン濃縮", "プルトニウム",
            "放射性物質", "臨界", "核", "濃縮ウラン",
            "nuclear", "reactor", "uranium", "plutonium", "fission",
            "fusion", "criticality", "enrichment", "radioactive",
        ],
        "risk_level": "HIGH",
    },
    {
        "id": "cyber_crypto",
        "label": "サイバーセキュリティ・暗号技術",
        "article": "第22条第5号",
        "keywords": [
            "暗号アルゴリズム", "暗号鍵", "サイバー攻撃", "サイバー防衛",
            "量子暗号", "ゼロデイ", "マルウェア", "サイドチャネル",
            "cipher", "cryptography", "quantum encryption", "cyber warfare",
            "zero-day", "malware", "side channel", "quantum key distribution",
        ],
        "risk_level": "HIGH",
    },
    {
        "id": "advanced_materials",
        "label": "先端材料・超耐熱・ステルス材料",
        "article": "第22条第6号",
        "keywords": [
            "電波吸収材", "ステルス材料", "耐熱材料", "超高温合金",
            "複合装甲", "セラミック装甲", "チョバム",
            "radar absorbing material", "stealth material", "hypersonic thermal",
            "superalloy", "composite armor", "ceramic armor",
        ],
        "risk_level": "HIGH",
    },
    {
        "id": "semiconductor_military",
        "label": "先端半導体（防衛・宇宙応用）",
        "article": "第22条第7号",
        "keywords": [
            "EMP耐性", "耐放射線", "軍用半導体", "化合物半導体（軍）",
            "radiation hardened", "EMP resistant", "military grade IC",
            "rad-hard", "space-grade semiconductor",
        ],
        "risk_level": "MEDIUM",
    },
    {
        "id": "quantum",
        "label": "量子技術（量子コンピュータ・量子センサ）",
        "article": "第22条第8号",
        "keywords": [
            "量子コンピュータ", "量子センサ", "量子通信", "量子鍵配送",
            "量子もつれ", "量子アニーリング",
            "quantum computer", "quantum sensor", "quantum communication",
            "QKD", "qubit", "quantum annealing", "quantum entanglement",
        ],
        "risk_level": "MEDIUM",
    },
    {
        "id": "ai_autonomous",
        "label": "AI・自律システム（軍事応用）",
        "article": "第22条第9号",
        "keywords": [
            "自律型兵器", "自律戦闘", "目標自動認識", "LAWS",
            "autonomous weapon", "lethal autonomous", "target recognition",
            "AI warfare", "kill chain", "autonomous combat",
        ],
        "risk_level": "MEDIUM",
    },
    {
        "id": "biosecurity",
        "label": "バイオ・化学（防衛応用）",
        "article": "第22条第10号",
        "keywords": [
            "生物兵器", "化学兵器", "毒素", "病原体（軍事）",
            "biological weapon", "chemical weapon", "toxin", "BW agent",
            "CW agent", "dual-use biology",
        ],
        "risk_level": "HIGH",
    },
]


@dataclass
class PatentDisclosureRisk:
    risk_level: str                              # "HIGH" | "MEDIUM" | "NONE"
    matched_categories: list[dict] = field(default_factory=list)
    recommended_action: str = ""
    law_reference: str = "経済安全保障推進法 第4章（2024年5月施行）・Cabinet Order Art.22"
    pre_filing_notice_required: bool = False


def check_patent_disclosure_risk(
    title: str = "",
    description: str = "",
) -> PatentDisclosureRisk:
    """
    R&Dプロジェクトのタイトル・説明から特許非公開リスクを判定する。

    Returns:
        PatentDisclosureRisk — リスクレベル・該当カテゴリ・推奨アクション
    """
    search_text = " ".join(filter(None, [title, description])).lower()

    matched: list[dict] = []
    for cat in _SPECIFIED_TECH_CATEGORIES:
        for kw in cat["keywords"]:
            if kw.lower() in search_text:
                matched.append({
                    "id":              cat["id"],
                    "label":           cat["label"],
                    "article":         cat["article"],
                    "risk_level":      cat["risk_level"],
                    "matched_keyword": kw,
                })
                break  # カテゴリごとに最初のマッチのみ

    if not matched:
        return PatentDisclosureRisk(
            risk_level="NONE",
            recommended_action=(
                "現時点で特定技術分野への該当は確認されません。"
                "研究内容に変化があれば再確認してください。"
            ),
        )

    has_high = any(m["risk_level"] == "HIGH" for m in matched)
    overall_level = "HIGH" if has_high else "MEDIUM"

    if overall_level == "HIGH":
        action = (
            "このプロジェクトは経済安全保障推進法の特定技術分野（高リスク）に"
            "該当する可能性があります。特許出願前に①知財部門への相談②内閣府への"
            "事前確認が必要です。外国出願も禁止となる場合があります。"
            "違反した場合は2年以下の懲役または100万円以下の罰金。"
        )
    else:
        action = (
            "特定技術分野への中程度のリスクが確認されました。"
            "特許出願前に知財部門へ相談し、内閣府への届出要否を確認することを推奨します。"
        )

    return PatentDisclosureRisk(
        risk_level=overall_level,
        matched_categories=matched,
        recommended_action=action,
        pre_filing_notice_required=(overall_level == "HIGH"),
    )
