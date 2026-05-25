"""
build_country_control_matrix.py
================================
EAR Country Control Matrix を構築する。

出力: data/ontology/country_control_matrix.json
  - 主要国のカントリーグループ分類
  - ECCN カテゴリ × カントリーグループ → ライセンス要否サマリー
  - 外為法「外国為替及び外国貿易法」仕向地ゾーン分類
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "platform-core"))

from platform_core.ontology.rules.ear_reason_engine import (
    _COUNTRY_GROUP_A,
    _COUNTRY_GROUP_D1,
    _COUNTRY_GROUP_E1,
    _STA_ELIGIBLE,
    _CATEGORY_REASONS,
    _get_country_group,
    _get_reasons,
    _get_applicable_exceptions,
    evaluate_ear,
    EARContext,
)

OUT_DIR = _ROOT / "data" / "ontology"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── 主要国マスタ ──────────────────────────────────────────────────────────────
# ISO 3166-1 alpha-2 → {name_ja, name_en, region, fefta_zone, notes}
# fefta_zone: "white" / "普通地域" / "懸念地域" / "禁輸地域"
_COUNTRY_MASTER: dict[str, dict] = {
    # ── 同盟国・友好国 (Group A) ──
    "JP": {"name_ja": "日本", "name_en": "Japan", "region": "Asia-Pacific", "fefta_zone": "white"},
    "US": {"name_ja": "米国", "name_en": "United States", "region": "North America", "fefta_zone": "white"},
    "CA": {"name_ja": "カナダ", "name_en": "Canada", "region": "North America", "fefta_zone": "white"},
    "GB": {"name_ja": "英国", "name_en": "United Kingdom", "region": "Europe", "fefta_zone": "white"},
    "DE": {"name_ja": "ドイツ", "name_en": "Germany", "region": "Europe", "fefta_zone": "white"},
    "FR": {"name_ja": "フランス", "name_en": "France", "region": "Europe", "fefta_zone": "white"},
    "IT": {"name_ja": "イタリア", "name_en": "Italy", "region": "Europe", "fefta_zone": "white"},
    "ES": {"name_ja": "スペイン", "name_en": "Spain", "region": "Europe", "fefta_zone": "white"},
    "NL": {"name_ja": "オランダ", "name_en": "Netherlands", "region": "Europe", "fefta_zone": "white"},
    "BE": {"name_ja": "ベルギー", "name_en": "Belgium", "region": "Europe", "fefta_zone": "white"},
    "SE": {"name_ja": "スウェーデン", "name_en": "Sweden", "region": "Europe", "fefta_zone": "white"},
    "NO": {"name_ja": "ノルウェー", "name_en": "Norway", "region": "Europe", "fefta_zone": "white"},
    "DK": {"name_ja": "デンマーク", "name_en": "Denmark", "region": "Europe", "fefta_zone": "white"},
    "FI": {"name_ja": "フィンランド", "name_en": "Finland", "region": "Europe", "fefta_zone": "white"},
    "AT": {"name_ja": "オーストリア", "name_en": "Austria", "region": "Europe", "fefta_zone": "white"},
    "CH": {"name_ja": "スイス", "name_en": "Switzerland", "region": "Europe", "fefta_zone": "white"},
    "AU": {"name_ja": "オーストラリア", "name_en": "Australia", "region": "Asia-Pacific", "fefta_zone": "white"},
    "NZ": {"name_ja": "ニュージーランド", "name_en": "New Zealand", "region": "Asia-Pacific", "fefta_zone": "white"},
    "PL": {"name_ja": "ポーランド", "name_en": "Poland", "region": "Europe", "fefta_zone": "white"},
    "CZ": {"name_ja": "チェコ", "name_en": "Czech Republic", "region": "Europe", "fefta_zone": "white"},
    "HU": {"name_ja": "ハンガリー", "name_en": "Hungary", "region": "Europe", "fefta_zone": "white"},
    "GR": {"name_ja": "ギリシャ", "name_en": "Greece", "region": "Europe", "fefta_zone": "white"},
    "PT": {"name_ja": "ポルトガル", "name_en": "Portugal", "region": "Europe", "fefta_zone": "white"},
    "IE": {"name_ja": "アイルランド", "name_en": "Ireland", "region": "Europe", "fefta_zone": "white"},
    "LU": {"name_ja": "ルクセンブルク", "name_en": "Luxembourg", "region": "Europe", "fefta_zone": "white"},
    "IS": {"name_ja": "アイスランド", "name_en": "Iceland", "region": "Europe", "fefta_zone": "white"},
    "TR": {"name_ja": "トルコ", "name_en": "Turkey", "region": "Middle East/Europe", "fefta_zone": "white"},
    # ── アジア (主に Group B) ──
    "KR": {"name_ja": "韓国", "name_en": "South Korea", "region": "Asia-Pacific", "fefta_zone": "普通地域"},
    "TW": {"name_ja": "台湾", "name_en": "Taiwan", "region": "Asia-Pacific", "fefta_zone": "普通地域"},
    "SG": {"name_ja": "シンガポール", "name_en": "Singapore", "region": "Asia-Pacific", "fefta_zone": "普通地域"},
    "MY": {"name_ja": "マレーシア", "name_en": "Malaysia", "region": "Asia-Pacific", "fefta_zone": "普通地域"},
    "TH": {"name_ja": "タイ", "name_en": "Thailand", "region": "Asia-Pacific", "fefta_zone": "普通地域"},
    "PH": {"name_ja": "フィリピン", "name_en": "Philippines", "region": "Asia-Pacific", "fefta_zone": "普通地域"},
    "ID": {"name_ja": "インドネシア", "name_en": "Indonesia", "region": "Asia-Pacific", "fefta_zone": "普通地域"},
    "HK": {"name_ja": "香港", "name_en": "Hong Kong", "region": "Asia-Pacific", "fefta_zone": "普通地域",
           "notes": "2020年以降、米国はHKをCNと同等扱い。実務上D:1相当で取り扱うことを推奨。"},
    # ── 懸念国 (Group D:1) ──
    "CN": {"name_ja": "中国", "name_en": "China", "region": "Asia-Pacific", "fefta_zone": "懸念地域",
           "notes": "EAR D:1。半導体・AIチップ等に追加規制（Entity List含む）。外為法 地域別輸出管理強化。"},
    "RU": {"name_ja": "ロシア", "name_en": "Russia", "region": "Europe/Asia", "fefta_zone": "懸念地域",
           "notes": "2022年ウクライナ侵攻以降の大幅規制強化。EAR D:1 + 広範な制裁。"},
    "BY": {"name_ja": "ベラルーシ", "name_en": "Belarus", "region": "Europe", "fefta_zone": "懸念地域",
           "notes": "ロシア支援国。EAR D:1。"},
    "IN": {"name_ja": "インド", "name_en": "India", "region": "South Asia", "fefta_zone": "懸念地域",
           "notes": "D:1 だが Quad パートナー。一部品目は STA/NLR 適用可能。"},
    "PK": {"name_ja": "パキスタン", "name_en": "Pakistan", "region": "South Asia", "fefta_zone": "懸念地域",
           "notes": "核・ミサイル技術懸念。NP/MT 品目は許可必要。"},
    "EG": {"name_ja": "エジプト", "name_en": "Egypt", "region": "Middle East/Africa", "fefta_zone": "懸念地域"},
    "SA": {"name_ja": "サウジアラビア", "name_en": "Saudi Arabia", "region": "Middle East", "fefta_zone": "懸念地域"},
    "AE": {"name_ja": "UAE（アラブ首長国連邦）", "name_en": "United Arab Emirates", "region": "Middle East", "fefta_zone": "懸念地域",
           "notes": "再輸出ハブとして要注意。転送先が懸念国の場合は追加確認必要。"},
    "VN": {"name_ja": "ベトナム", "name_en": "Vietnam", "region": "Asia-Pacific", "fefta_zone": "懸念地域"},
    "MM": {"name_ja": "ミャンマー", "name_en": "Myanmar", "region": "Asia-Pacific", "fefta_zone": "懸念地域",
           "notes": "2021年クーデター後の軍政。制裁あり。"},
    "IL": {"name_ja": "イスラエル", "name_en": "Israel", "region": "Middle East", "fefta_zone": "懸念地域"},
    "QA": {"name_ja": "カタール", "name_en": "Qatar", "region": "Middle East", "fefta_zone": "懸念地域"},
    "KW": {"name_ja": "クウェート", "name_en": "Kuwait", "region": "Middle East", "fefta_zone": "懸念地域"},
    "VE": {"name_ja": "ベネズエラ", "name_en": "Venezuela", "region": "Latin America", "fefta_zone": "懸念地域",
           "notes": "OFAC制裁対象政権関連取引に注意。"},
    # ── 禁輸国 (Group E:1) ──
    "KP": {"name_ja": "北朝鮮", "name_en": "North Korea", "region": "Asia-Pacific", "fefta_zone": "禁輸地域",
           "notes": "包括的禁輸。EAR E:1・OFAC包括制裁・外為法 特定国家。"},
    "IR": {"name_ja": "イラン", "name_en": "Iran", "region": "Middle East", "fefta_zone": "禁輸地域",
           "notes": "包括的禁輸。核・ミサイル・テロ関連制裁。"},
    "CU": {"name_ja": "キューバ", "name_en": "Cuba", "region": "Latin America", "fefta_zone": "禁輸地域",
           "notes": "米国包括制裁（CACR）。"},
    "SY": {"name_ja": "シリア", "name_en": "Syria", "region": "Middle East", "fefta_zone": "禁輸地域",
           "notes": "化学兵器使用・人権侵害制裁。"},
    "SD": {"name_ja": "スーダン", "name_en": "Sudan", "region": "Africa", "fefta_zone": "禁輸地域",
           "notes": "OFAC制裁。軍・武装勢力関連。"},
}


def _classify_country(code: str) -> dict:
    cg = _get_country_group(code)
    sta = code.upper() in _STA_ELIGIBLE
    master = _COUNTRY_MASTER.get(code.upper(), {})
    return {
        "code": code.upper(),
        "name_ja": master.get("name_ja", code),
        "name_en": master.get("name_en", code),
        "region": master.get("region", "Unknown"),
        "country_group": cg,
        "sta_eligible": sta,
        "fefta_zone": master.get("fefta_zone", "普通地域"),
        "notes": master.get("notes", ""),
    }


def _build_matrix_summary() -> dict:
    """ECCN カテゴリ prefix × country_group → verdict サマリーテーブル。"""
    groups = ["A", "B", "D:1", "E:1"]
    sample_countries = {"A": "JP", "B": "KR", "D:1": "CN", "E:1": "KP"}
    summary: dict[str, dict] = {}

    for prefix, reasons in _CATEGORY_REASONS.items():
        eccn = f"{prefix}001"  # representative ECCN
        row: dict[str, str] = {}
        for cg in groups:
            country = sample_countries[cg]
            ctx = EARContext(
                transaction_id=0,
                destination_country=country,
                eccn=eccn,
                end_user_type="commercial",
            )
            j = evaluate_ear(ctx)
            row[cg] = j.verdict
        summary[prefix] = {
            "reasons": reasons,
            "verdicts_by_group": row,
        }
    return summary


def build_matrix() -> dict:
    countries_out: dict[str, dict] = {}

    # まずマスタに定義された全国を処理
    for code in _COUNTRY_MASTER:
        countries_out[code] = _classify_country(code)

    # ear_reason_engine の Group A/D1/E1 に含まれるがマスタ未定義の国も追加
    all_known = _COUNTRY_GROUP_A | _COUNTRY_GROUP_D1 | _COUNTRY_GROUP_E1
    for code in sorted(all_known):
        if code not in countries_out:
            countries_out[code] = _classify_country(code)

    matrix_summary = _build_matrix_summary()

    # 統計
    group_stats: dict[str, int] = {"A": 0, "B": 0, "D:1": 0, "E:1": 0}
    for c in countries_out.values():
        g = c["country_group"]
        group_stats[g] = group_stats.get(g, 0) + 1

    return {
        "_version": "1.0",
        "_built_at": __import__("datetime").datetime.utcnow().isoformat() + "+00:00",
        "_description": "EAR Country Control Matrix — 国別輸出規制分類（platform-core API 用）",
        "stats": {
            "total_countries": len(countries_out),
            "by_group": group_stats,
        },
        "country_groups": {
            "A": {
                "label_ja": "グループA（信頼国・同盟国）",
                "label_en": "Country Group A (White Countries)",
                "description_ja": "Wassenaar・NSG 等の多国間輸出管理体制加盟国。STA 等の例外が広く適用可能。",
                "license_tendency": "NLR / STA",
            },
            "B": {
                "label_ja": "グループB（標準国）",
                "label_en": "Country Group B (Standard Countries)",
                "description_ja": "上記以外の標準国。GBS/LVS 等の例外が適用可能な場合あり。",
                "license_tendency": "GBS / LVS 例外",
            },
            "D:1": {
                "label_ja": "グループD:1（国家安全保障懸念国）",
                "label_en": "Country Group D:1 (NS Concern Countries)",
                "description_ja": "NS 品目について許可審査が厳格化。MT/NP/CB 品目はほぼ許可必要。",
                "license_tendency": "LICENSE_REQUIRED（MT/NP/CB品目）/ CIV 例外（NS品目・民需）",
            },
            "E:1": {
                "label_ja": "グループE:1（禁輸国）",
                "label_en": "Country Group E:1 (Embargoed Countries)",
                "description_ja": "原則として全品目に輸出禁止。包括的制裁対象。",
                "license_tendency": "PROHIBITED",
            },
        },
        "countries": countries_out,
        "matrix_summary": matrix_summary,
        "fefta_zones": {
            "white": {
                "label_ja": "ホワイト国（輸出令別表第3の2）",
                "description_ja": "安全保障上信頼できる国。特定品目のリスト規制包括許可が適用可能。",
                "countries": [k for k, v in countries_out.items() if v["fefta_zone"] == "white"],
            },
            "普通地域": {
                "label_ja": "普通地域",
                "description_ja": "個別に許可判断が必要な標準地域。",
                "countries": [k for k, v in countries_out.items() if v["fefta_zone"] == "普通地域"],
            },
            "懸念地域": {
                "label_ja": "懸念地域",
                "description_ja": "外為法上の輸出審査強化対象地域。技術流出リスクが高い。",
                "countries": [k for k, v in countries_out.items() if v["fefta_zone"] == "懸念地域"],
            },
            "禁輸地域": {
                "label_ja": "禁輸地域（外為法 特定地域・包括制裁）",
                "description_ja": "EAR E:1 および外為法の包括的輸出禁止地域。",
                "countries": [k for k, v in countries_out.items() if v["fefta_zone"] == "禁輸地域"],
            },
        },
    }


if __name__ == "__main__":
    print("Building Country Control Matrix...")
    matrix = build_matrix()
    out_path = OUT_DIR / "country_control_matrix.json"
    out_path.write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
    stats = matrix["stats"]
    print(f"Done: {out_path}")
    print(f"  Countries: {stats['total_countries']} (A:{stats['by_group']['A']} B:{stats['by_group']['B']} D:1:{stats['by_group']['D:1']} E:1:{stats['by_group']['E:1']})")
    print(f"  Matrix summary: {len(matrix['matrix_summary'])} ECCN prefixes")
    print(f"  FEFTA zones: white={len(matrix['fefta_zones']['white']['countries'])} 懸念={len(matrix['fefta_zones']['懸念地域']['countries'])} 禁輸={len(matrix['fefta_zones']['禁輸地域']['countries'])}")
