"""EPA/FTA 特恵税率 API ルーター。

エンドポイント:
  GET  /api/fta/agreements              協定一覧
  GET  /api/fta/rates                   税率検索（hs_code × agreement_code）
  GET  /api/fta/check                   HS コードと仕向地国から適用可能な特恵税率を返す
  GET  /api/fta/rules-of-origin         原産地規則一覧（協定別 × HS章別）
  POST /api/fta/determine-origin        原産性判定（製造工程情報を評価）
  GET  /api/fta/certificates            証明書要件（協定別）
  POST /api/fta/seed                    初期データ投入（管理者用）
  GET  /ui/fta-check                    UI ページ
"""

import pathlib
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..pg_session import get_pg_db
from platform_core.models.fta import FtaAgreement, FtaRate

_TEMPLATES_DIR = pathlib.Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(tags=["fta"])

# ── 原産地規則（Rules of Origin）データ ────────────────────────────
# 各協定の主要な原産地規則（HS章ベース、代表的規則）
# rule_type: wholly_obtained | cc | ctsh | cts | rvc | specific
_RULES_OF_ORIGIN: dict[str, list[dict[str, Any]]] = {
    "JAEPA": [
        {"hs_chapter": "01-24", "rule_type": "wholly_obtained",
         "description": "第1-24類（農水産品）: 一般的に完全取得品（EU域内生産・漁獲）",
         "rvc_threshold": None, "note": "加工基準（processing rule）が品目別に規定"},
        {"hs_chapter": "25-49", "rule_type": "ctsh",
         "description": "第25-49類（化学品・プラスチック・紙）: 関税番号変更（CTSH: 6桁）ルールが基本",
         "rvc_threshold": None, "note": "一部品目は付加価値基準（RVC 45%）との選択制"},
        {"hs_chapter": "50-63", "rule_type": "cc",
         "description": "第50-63類（繊維・衣類）: 二工程ルール（糸→布→製品の加工を要求）",
         "rvc_threshold": None, "note": "外装縫製による変更のみでは不可"},
        {"hs_chapter": "64-83", "rule_type": "ctsh",
         "description": "第64-83類（履物・金属製品）: CTSH（関税番号6桁変更）",
         "rvc_threshold": None, "note": ""},
        {"hs_chapter": "84-85", "rule_type": "rvc",
         "description": "第84-85類（機械・電気機器）: 付加価値基準 RVC ≥ 40%（控除方式）またはCTSH",
         "rvc_threshold": 40, "note": "最終製品価格に占める原産材料の割合"},
        {"hs_chapter": "86-92", "rule_type": "ctsh",
         "description": "第86-92類（輸送機器・精密機器）: CTSH または RVC ≥ 45%",
         "rvc_threshold": 45, "note": "自動車（87類）は特別規定あり"},
        {"hs_chapter": "93-99", "rule_type": "ctsh",
         "description": "第93-99類（武器・芸術品等）: CTSH",
         "rvc_threshold": None, "note": ""},
    ],
    "RCEP": [
        {"hs_chapter": "01-24", "rule_type": "wholly_obtained",
         "description": "第1-24類: 完全取得品または実質的変換（品目別規則 PSR）",
         "rvc_threshold": None, "note": "RCEP PSRは品目別に細かく規定"},
        {"hs_chapter": "25-83", "rule_type": "rvc",
         "description": "第25-83類: RVC ≥ 40%（積み上げ方式）またはCTSH",
         "rvc_threshold": 40, "note": "ASEAN原産材料も域内材料として算入可"},
        {"hs_chapter": "84-85", "rule_type": "rvc",
         "description": "第84-85類: RVC ≥ 40% または CTSH",
         "rvc_threshold": 40, "note": "電気機器は特に注意（部品の原産性確認が重要）"},
        {"hs_chapter": "86-97", "rule_type": "ctsh",
         "description": "第86-97類: CTSH（6桁）または RVC ≥ 40%",
         "rvc_threshold": 40, "note": ""},
    ],
    "CPTPP": [
        {"hs_chapter": "01-24", "rule_type": "wholly_obtained",
         "description": "第1-24類: 完全取得品またはPSR（品目別規則）",
         "rvc_threshold": None, "note": "農産品は原則完全取得"},
        {"hs_chapter": "25-49", "rule_type": "ctsh",
         "description": "第25-49類: CTSH（関税番号6桁変更）が一般ルール",
         "rvc_threshold": None, "note": ""},
        {"hs_chapter": "50-63", "rule_type": "specific",
         "description": "第50-63類（繊維）: 原則「糸から」ルール（yarns-forward）",
         "rvc_threshold": None, "note": "TPPの繊維ルールは最も厳格なレベル"},
        {"hs_chapter": "64-97", "rule_type": "rvc",
         "description": "第64-97類: RVC ≥ 45%（純費用方式）または CTSH",
         "rvc_threshold": 45, "note": "自動車（87類）は特別規則：RVC ≥ 45%（段階的引き上げ）"},
    ],
    "UKJFTA": [
        {"hs_chapter": "01-97", "rule_type": "ctsh",
         "description": "JAEPA と同等の原産地規則を維持",
         "rvc_threshold": None, "note": "Brexit後、日英間はJAEPA相当水準を適用"},
    ],
    "USJTA": [
        {"hs_chapter": "01-24", "rule_type": "wholly_obtained",
         "description": "第1-24類（農産品中心）: 完全取得品",
         "rvc_threshold": None, "note": "日米貿易協定の対象は農産品・工業品の一部"},
        {"hs_chapter": "25-97", "rule_type": "ctsh",
         "description": "第25-97類: CTSH または各品目の特別規定",
         "rvc_threshold": None, "note": "包括的FTAではないため品目カバレッジに注意"},
    ],
    "JASINGFTA": [
        {"hs_chapter": "01-97", "rule_type": "rvc",
         "description": "一般規則: RVC ≥ 60%（FOB価格基準）",
         "rvc_threshold": 60, "note": "特定品目はより低い閾値（40-50%）"},
    ],
    "JAMEPA": [
        {"hs_chapter": "01-97", "rule_type": "rvc",
         "description": "一般規則: RVC ≥ 40%（FOB価格基準）またはCC（4桁変更）",
         "rvc_threshold": 40, "note": ""},
    ],
    "JAINDEPA": [
        {"hs_chapter": "01-97", "rule_type": "rvc",
         "description": "一般規則: RVC ≥ 35%（FOB価格基準）またはCTSH",
         "rvc_threshold": 35, "note": "インドEPAは比較的低い付加価値要件"},
    ],
    "JAAUSFTA": [
        {"hs_chapter": "01-24", "rule_type": "wholly_obtained",
         "description": "第1-24類: 完全取得品",
         "rvc_threshold": None, "note": ""},
        {"hs_chapter": "25-97", "rule_type": "rvc",
         "description": "第25-97類: RVC ≥ 40% またはCC",
         "rvc_threshold": 40, "note": ""},
    ],
    "JATHAIEPA": [
        {"hs_chapter": "01-97", "rule_type": "rvc",
         "description": "一般規則: RVC ≥ 40%（FOB価格基準）またはCC",
         "rvc_threshold": 40, "note": ""},
    ],
    "JAINDEPA2": [
        {"hs_chapter": "01-97", "rule_type": "rvc",
         "description": "一般規則: RVC ≥ 35% またはCTSH",
         "rvc_threshold": 35, "note": ""},
    ],
}

# ── 証明書要件データ ────────────────────────────────────────────────
_CERTIFICATE_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "JAEPA": {
        "certificate_name": "原産地申告（Statement on Origin）",
        "certificate_code": "SOO",
        "issuing_body": "承認輸出者（自己申告制度）または日本商工会議所",
        "form_name": "原産地申告文（EUR.1形式に代わる自己申告）",
        "validity_period_months": 12,
        "threshold_eur": None,
        "self_declaration": True,
        "approved_exporter_required": True,
        "required_documents": [
            "原産地申告文（インボイス上に記載またはフォームB）",
            "原産性を証明する書類（材料費内訳、製造工程記録）",
            "承認輸出者番号（AEO/承認輸出者登録）",
        ],
        "notes": "JAEPA（日EU EPA）では EUR.1 様式廃止。自己申告制度（Statement on Origin）を採用。6,000ユーロ超の場合は承認輸出者のみ申告可。",
    },
    "RCEP": {
        "certificate_name": "原産地証明書（RCEP様式）",
        "certificate_code": "RCEP-FORM",
        "issuing_body": "日本商工会議所（認定された発給機関）",
        "form_name": "RCEP 原産地証明書（Form RCEP）",
        "validity_period_months": 12,
        "threshold_eur": None,
        "self_declaration": True,
        "approved_exporter_required": False,
        "required_documents": [
            "RCEP 原産地証明書（Form RCEP）または認定輸出者による原産地申告",
            "インボイス（品名・数量・価格）",
            "包装明細書（パッキングリスト）",
            "原産性証明書類（材料の原産地証明・RVC計算書）",
        ],
        "notes": "2022年1月発効。第三者証明（商工会議所）または自己申告（認定輸出者）が選択可。累積規定（RCEP域内材料をすべて自国産として算入）活用可能。",
    },
    "CPTPP": {
        "certificate_name": "原産地証明書（CPTPP自己申告）",
        "certificate_code": "CPTPP-SD",
        "issuing_body": "輸出者・生産者（自己申告制度）",
        "form_name": "CPTPP 原産地申告（特定の様式なし、任意様式）",
        "validity_period_months": 12,
        "threshold_eur": None,
        "self_declaration": True,
        "approved_exporter_required": False,
        "required_documents": [
            "CPTPP 原産地証明書または原産地申告（インボイス、送り状等に記載）",
            "原産資格申告（Certification of Origin）",
            "原産性証明書類（製造記録・材料費内訳）",
        ],
        "notes": "CPTPP では特定様式の証明書は不要。輸出者・生産者・輸入者のいずれかが原産地申告（Certification）を作成できる。申告内容は最低限の必要記載事項を満たすこと。",
    },
    "UKJFTA": {
        "certificate_name": "原産地申告（日英EPA）",
        "certificate_code": "UKJFTA-SOO",
        "issuing_body": "承認輸出者（自己申告）",
        "form_name": "JAEPA 相当の原産地申告",
        "validity_period_months": 12,
        "threshold_eur": 6000,
        "self_declaration": True,
        "approved_exporter_required": True,
        "required_documents": [
            "原産地申告文（インボイス・デリバリーノートに記載）",
            "承認輸出者番号",
            "原産性証明書類",
        ],
        "notes": "日英EPA は JAEPA と同等水準の原産地規則・証明制度。6,000ポンド超は承認輸出者のみ自己申告可。",
    },
    "USJTA": {
        "certificate_name": "原産地証明書（日米貿易協定）",
        "certificate_code": "USJTA-COO",
        "issuing_body": "日本商工会議所",
        "form_name": "一般的な Certificate of Origin（COO）",
        "validity_period_months": 12,
        "threshold_eur": None,
        "self_declaration": False,
        "approved_exporter_required": False,
        "required_documents": [
            "原産地証明書（商工会議所発給）",
            "インボイス",
            "材料費内訳書",
        ],
        "notes": "日米貿易協定では第三者証明（商工会議所）が標準。自己申告制度は未整備。",
    },
    "JASINGFTA": {
        "certificate_name": "Form JP（日シンガポールEPA）",
        "certificate_code": "FORM-JP-SG",
        "issuing_body": "日本商工会議所または自己申告（承認輸出者）",
        "form_name": "Form JP",
        "validity_period_months": 12,
        "threshold_eur": None,
        "self_declaration": True,
        "approved_exporter_required": False,
        "required_documents": [
            "Form JP（原産地証明書）",
            "インボイス",
            "製品の原産性証明書類",
        ],
        "notes": "日本初のEPAとして2002年発効。Form JP は日本のEPA証明書の標準様式。",
    },
    "JAMEPA": {
        "certificate_name": "Form JP（日マレーシアEPA）",
        "certificate_code": "FORM-JP-MY",
        "issuing_body": "日本商工会議所",
        "form_name": "Form JP",
        "validity_period_months": 12,
        "threshold_eur": None,
        "self_declaration": False,
        "approved_exporter_required": False,
        "required_documents": [
            "Form JP",
            "インボイス",
            "パッキングリスト",
            "RVC計算書（付加価値基準の場合）",
        ],
        "notes": "",
    },
    "RCEP": {
        "certificate_name": "RCEP 原産地証明書",
        "certificate_code": "FORM-RCEP",
        "issuing_body": "日本商工会議所または認定輸出者（自己申告）",
        "form_name": "Form RCEP",
        "validity_period_months": 12,
        "threshold_eur": None,
        "self_declaration": True,
        "approved_exporter_required": False,
        "required_documents": [
            "Form RCEP または原産地申告",
            "インボイス",
            "RVC計算書（必要な場合）",
            "累積規定活用の場合は相手国原産証明",
        ],
        "notes": "RCEP では全加盟国向けに単一フォームを使用。",
    },
}

# ── 原産性判定ロジック ──────────────────────────────────────────────

class OriginDetermineRequest(BaseModel):
    hs_code: str
    agreement_code: str
    origin_country: str = "JP"
    rvc_pct: float | None = None          # 付加価値割合（%）
    is_wholly_obtained: bool = False       # 完全取得品か
    tariff_shift_satisfied: bool | None = None  # 関税番号変更基準を満たすか
    manufacturing_description: str | None = None


def _get_applicable_rules(agreement_code: str, hs_code: str) -> list[dict[str, Any]]:
    rules = _RULES_OF_ORIGIN.get(agreement_code, [])
    if not rules:
        return []
    # HS chapter（2桁）でマッチング
    try:
        hs_clean = hs_code.replace(".", "").replace(" ", "")
        chapter = int(hs_clean[:2])
    except (ValueError, IndexError):
        return rules

    matched = []
    for rule in rules:
        ch_range = rule.get("hs_chapter", "")
        # "84-85" や "01-24" "01-97" 形式を解析
        try:
            parts = ch_range.split("-")
            lo = int(parts[0])
            hi = int(parts[1]) if len(parts) > 1 else lo
            if lo <= chapter <= hi:
                matched.append(rule)
        except (ValueError, IndexError):
            matched.append(rule)
    return matched if matched else rules

# ── シードデータ ────────────────────────────────────────────────────

_AGREEMENTS_SEED = [
    {
        "code": "JAEPA",
        "name_ja": "日EU経済連携協定",
        "name_en": "Japan-EU Economic Partnership Agreement",
        "partner_countries": "AT,BE,BG,HR,CY,CZ,DK,EE,FI,FR,DE,GR,HU,IE,IT,LV,LT,LU,MT,NL,PL,PT,RO,SK,SI,ES,SE",
        "origin_country": "JP",
        "effective_date": "2019-02-01",
        "status": "active",
        "notes": "2019年2月発効。2033年までに関税の97%を撤廃。",
    },
    {
        "code": "RCEP",
        "name_ja": "地域的な包括的経済連携協定（RCEP）",
        "name_en": "Regional Comprehensive Economic Partnership",
        "partner_countries": "AU,BN,KH,CN,ID,LA,MY,MM,NZ,PH,SG,KR,TH,VN",
        "origin_country": "JP",
        "effective_date": "2022-01-01",
        "status": "active",
        "notes": "2022年1月発効。ASEAN10カ国＋日中韓豪NZ。",
    },
    {
        "code": "CPTPP",
        "name_ja": "環太平洋パートナーシップに関する包括的及び先進的な協定（CPTPP）",
        "name_en": "Comprehensive and Progressive Agreement for Trans-Pacific Partnership",
        "partner_countries": "AU,BN,CA,CL,MX,NZ,PE,SG,VN,MY,GB",
        "origin_country": "JP",
        "effective_date": "2018-12-30",
        "status": "active",
        "notes": "2018年12月発効（日本）。英国は2024年12月加入。",
    },
    {
        "code": "UKJFTA",
        "name_ja": "日英包括的経済連携協定",
        "name_en": "Japan-UK Comprehensive Economic Partnership Agreement",
        "partner_countries": "GB",
        "origin_country": "JP",
        "effective_date": "2021-01-01",
        "status": "active",
        "notes": "Brexit後、JAEPA相当水準を維持。2021年1月発効。",
    },
    {
        "code": "USJTA",
        "name_ja": "日米貿易協定",
        "name_en": "Japan-US Trade Agreement",
        "partner_countries": "US",
        "origin_country": "JP",
        "effective_date": "2020-01-01",
        "status": "active",
        "notes": "2020年1月発効。農産品・工業品の関税削減。包括的FTAは未締結。",
    },
    {
        "code": "JASINGFTA",
        "name_ja": "日シンガポール新時代経済連携協定",
        "name_en": "Japan-Singapore Economic Partnership Agreement",
        "partner_countries": "SG",
        "origin_country": "JP",
        "effective_date": "2002-11-30",
        "status": "active",
        "notes": "日本初のEPA（2002年発効）。",
    },
    {
        "code": "JAMEPA",
        "name_ja": "日マレーシア経済連携協定",
        "name_en": "Japan-Malaysia Economic Partnership Agreement",
        "partner_countries": "MY",
        "origin_country": "JP",
        "effective_date": "2006-07-13",
        "status": "active",
        "notes": "2006年7月発効。",
    },
    {
        "code": "JATHFTA",
        "name_ja": "日タイ経済連携協定",
        "name_en": "Japan-Thailand Economic Partnership Agreement",
        "partner_countries": "TH",
        "origin_country": "JP",
        "effective_date": "2007-11-01",
        "status": "active",
        "notes": "2007年11月発効。",
    },
    {
        "code": "JAVNEPA",
        "name_ja": "日ベトナム経済連携協定",
        "name_en": "Japan-Vietnam Economic Partnership Agreement",
        "partner_countries": "VN",
        "origin_country": "JP",
        "effective_date": "2009-10-01",
        "status": "active",
        "notes": "2009年10月発効。",
    },
    {
        "code": "JAINEPA",
        "name_ja": "日インド包括的経済連携協定",
        "name_en": "Japan-India Comprehensive Economic Partnership Agreement",
        "partner_countries": "IN",
        "origin_country": "JP",
        "effective_date": "2011-08-01",
        "status": "active",
        "notes": "2011年8月発効。",
    },
    {
        "code": "JAPHEPA",
        "name_ja": "日フィリピン経済連携協定",
        "name_en": "Japan-Philippines Economic Partnership Agreement",
        "partner_countries": "PH",
        "origin_country": "JP",
        "effective_date": "2008-12-11",
        "status": "active",
        "notes": "2008年12月発効。電子部品・機械類の関税撤廃。",
    },
    {
        "code": "JAIDFTA",
        "name_ja": "日インドネシア経済連携協定",
        "name_en": "Japan-Indonesia Economic Partnership Agreement",
        "partner_countries": "ID",
        "origin_country": "JP",
        "effective_date": "2008-07-01",
        "status": "active",
        "notes": "2008年7月発効。",
    },
    {
        "code": "JAMNEPA",
        "name_ja": "日モンゴル経済連携協定",
        "name_en": "Japan-Mongolia Economic Partnership Agreement",
        "partner_countries": "MN",
        "origin_country": "JP",
        "effective_date": "2016-06-07",
        "status": "active",
        "notes": "2016年6月発効。鉱業・エネルギー分野を含む包括的EPA。",
    },
    {
        "code": "JAUAEPA",
        "name_ja": "日UAE包括的経済連携協定",
        "name_en": "Japan-UAE Comprehensive Economic Partnership Agreement",
        "partner_countries": "AE",
        "origin_country": "JP",
        "effective_date": "2023-09-01",
        "status": "active",
        "notes": "2023年9月発効。GCC諸国との初のEPA。",
    },
    {
        "code": "JAKHEPA",
        "name_ja": "日韓経済連携協定（交渉中）",
        "name_en": "Japan-Korea FTA (under negotiation)",
        "partner_countries": "KR",
        "origin_country": "JP",
        "effective_date": None,
        "status": "suspended",
        "notes": "2003年から交渉中・中断。現在はRCEP適用。",
    },
]

_RATES_SEED = [
    {"agreement_code": "JAEPA",  "hs_code": "8542.31", "hs_description": "電子集積回路（プロセッサー及びコントローラー）",
     "year": 2024, "mfn_rate_pct": 0.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "RCEP",   "hs_code": "8542.31", "hs_description": "電子集積回路（プロセッサー及びコントローラー）",
     "year": 2024, "mfn_rate_pct": 0.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "CPTPP",  "hs_code": "8542.31", "hs_description": "電子集積回路（プロセッサー及びコントローラー）",
     "year": 2024, "mfn_rate_pct": 0.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "JAEPA",  "hs_code": "9013.20", "hs_description": "レーザー（レーザーダイオードを除く）",
     "year": 2024, "mfn_rate_pct": 1.7, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "RCEP",   "hs_code": "9013.20", "hs_description": "レーザー（レーザーダイオードを除く）",
     "year": 2024, "mfn_rate_pct": 5.0, "preferential_rate_pct": 0.0, "staging_category": "B", "is_eliminated": False, "elimination_year": 2032},
    {"agreement_code": "CPTPP",  "hs_code": "9013.20", "hs_description": "レーザー（レーザーダイオードを除く）",
     "year": 2024, "mfn_rate_pct": 1.7, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "JAEPA",  "hs_code": "8803.30", "hs_description": "航空機の部分品（胴体・翼等）",
     "year": 2024, "mfn_rate_pct": 1.7, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "CPTPP",  "hs_code": "8803.30", "hs_description": "航空機の部分品（胴体・翼等）",
     "year": 2024, "mfn_rate_pct": 1.7, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "RCEP",   "hs_code": "8803.30", "hs_description": "航空機の部分品（胴体・翼等）",
     "year": 2024, "mfn_rate_pct": 3.3, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "JAEPA",  "hs_code": "8703.23", "hs_description": "乗用自動車（1500cc超3000cc以下）",
     "year": 2024, "mfn_rate_pct": 10.0, "preferential_rate_pct": 5.0, "staging_category": "B", "is_eliminated": False, "elimination_year": 2027,
     "notes": "2019年: 10%→8%→6%→4% と段階的削減、2027年完全撤廃予定"},
    {"agreement_code": "CPTPP",  "hs_code": "8703.23", "hs_description": "乗用自動車（1500cc超3000cc以下）",
     "year": 2024, "mfn_rate_pct": 6.5, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "RCEP",   "hs_code": "8703.23", "hs_description": "乗用自動車（1500cc超3000cc以下）",
     "year": 2024, "mfn_rate_pct": 25.0, "preferential_rate_pct": 10.0, "staging_category": "B", "is_eliminated": False, "elimination_year": 2035,
     "notes": "中国向け（RCEP）は段階的削減、完全撤廃まで時間を要する"},
    {"agreement_code": "JAEPA",  "hs_code": "7208.51", "hs_description": "鉄または非合金鋼の平板圧延品（厚さ4.75mm以上）",
     "year": 2024, "mfn_rate_pct": 0.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "RCEP",   "hs_code": "7208.51", "hs_description": "鉄または非合金鋼の平板圧延品（厚さ4.75mm以上）",
     "year": 2024, "mfn_rate_pct": 2.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "JAEPA",  "hs_code": "3004.90", "hs_description": "医薬品（その他のもの）",
     "year": 2024, "mfn_rate_pct": 0.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "RCEP",   "hs_code": "3004.90", "hs_description": "医薬品（その他のもの）",
     "year": 2024, "mfn_rate_pct": 0.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "JAEPA",  "hs_code": "9031.80", "hs_description": "測定機器・検査機器の部分品（その他）",
     "year": 2024, "mfn_rate_pct": 1.7, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "RCEP",   "hs_code": "9031.80", "hs_description": "測定機器・検査機器の部分品（その他）",
     "year": 2024, "mfn_rate_pct": 5.0, "preferential_rate_pct": 0.0, "staging_category": "B", "is_eliminated": False, "elimination_year": 2030},
    {"agreement_code": "JAEPA",  "hs_code": "2933.99", "hs_description": "窒素複素環式化合物（その他）",
     "year": 2024, "mfn_rate_pct": 5.5, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "RCEP",   "hs_code": "2933.99", "hs_description": "窒素複素環式化合物（その他）",
     "year": 2024, "mfn_rate_pct": 6.5, "preferential_rate_pct": 2.0, "staging_category": "B", "is_eliminated": False, "elimination_year": 2030},
    {"agreement_code": "JAEPA",  "hs_code": "8457.10", "hs_description": "マシニングセンター（金属加工用）",
     "year": 2024, "mfn_rate_pct": 2.5, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "RCEP",   "hs_code": "8457.10", "hs_description": "マシニングセンター（金属加工用）",
     "year": 2024, "mfn_rate_pct": 6.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "CPTPP",  "hs_code": "8457.10", "hs_description": "マシニングセンター（金属加工用）",
     "year": 2024, "mfn_rate_pct": 2.5, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    # ── 半導体製造装置（輸出管理重点品目）──────────────────────────────
    {"agreement_code": "JAEPA",  "hs_code": "8486.20", "hs_description": "半導体デバイス製造機器（リソグラフィ・エッチング・CVD等）",
     "year": 2024, "mfn_rate_pct": 1.7, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True,
     "notes": "EAR ECCN 3B001/3B002対象品。EU向けは即時撤廃。"},
    {"agreement_code": "RCEP",   "hs_code": "8486.20", "hs_description": "半導体デバイス製造機器（リソグラフィ・エッチング・CVD等）",
     "year": 2024, "mfn_rate_pct": 6.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True,
     "notes": "中国向け関税はRCEPで撤廃だが輸出許可要件に注意。"},
    {"agreement_code": "CPTPP",  "hs_code": "8486.20", "hs_description": "半導体デバイス製造機器（リソグラフィ・エッチング・CVD等）",
     "year": 2024, "mfn_rate_pct": 1.7, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "JAEPA",  "hs_code": "8486.40", "hs_description": "半導体製造用機器（検査・測定装置）",
     "year": 2024, "mfn_rate_pct": 1.7, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "RCEP",   "hs_code": "8486.40", "hs_description": "半導体製造用機器（検査・測定装置）",
     "year": 2024, "mfn_rate_pct": 3.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "CPTPP",  "hs_code": "8486.40", "hs_description": "半導体製造用機器（検査・測定装置）",
     "year": 2024, "mfn_rate_pct": 1.7, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    # ── 電子部品（半導体・ダイオード・トランジスタ）────────────────────
    {"agreement_code": "JAEPA",  "hs_code": "8541.10", "hs_description": "ダイオード（レーザーダイオードを除く）",
     "year": 2024, "mfn_rate_pct": 0.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "RCEP",   "hs_code": "8541.10", "hs_description": "ダイオード",
     "year": 2024, "mfn_rate_pct": 0.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "JAEPA",  "hs_code": "8541.21", "hs_description": "トランジスタ（消費電力1W未満）",
     "year": 2024, "mfn_rate_pct": 0.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "RCEP",   "hs_code": "8541.21", "hs_description": "トランジスタ（消費電力1W未満）",
     "year": 2024, "mfn_rate_pct": 0.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "JAEPA",  "hs_code": "8542.32", "hs_description": "電子集積回路（メモリ）",
     "year": 2024, "mfn_rate_pct": 0.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "RCEP",   "hs_code": "8542.32", "hs_description": "電子集積回路（メモリ）",
     "year": 2024, "mfn_rate_pct": 0.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "CPTPP",  "hs_code": "8542.32", "hs_description": "電子集積回路（メモリ）",
     "year": 2024, "mfn_rate_pct": 0.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    # ── 光学・精密機器（デュアルユース重点）───────────────────────────
    {"agreement_code": "JAEPA",  "hs_code": "9001.90", "hs_description": "光ファイバー・光学素子（その他）",
     "year": 2024, "mfn_rate_pct": 2.6, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "RCEP",   "hs_code": "9001.90", "hs_description": "光ファイバー・光学素子（その他）",
     "year": 2024, "mfn_rate_pct": 6.0, "preferential_rate_pct": 0.0, "staging_category": "B", "is_eliminated": False, "elimination_year": 2030},
    {"agreement_code": "CPTPP",  "hs_code": "9001.90", "hs_description": "光ファイバー・光学素子（その他）",
     "year": 2024, "mfn_rate_pct": 2.6, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "JAEPA",  "hs_code": "9013.80", "hs_description": "液晶デバイス・その他光学機器",
     "year": 2024, "mfn_rate_pct": 2.2, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "RCEP",   "hs_code": "9013.80", "hs_description": "液晶デバイス・その他光学機器",
     "year": 2024, "mfn_rate_pct": 4.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "JAEPA",  "hs_code": "9031.49", "hs_description": "測定・検査機器（光学式）",
     "year": 2024, "mfn_rate_pct": 1.7, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "RCEP",   "hs_code": "9031.49", "hs_description": "測定・検査機器（光学式）",
     "year": 2024, "mfn_rate_pct": 4.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    # ── 通信・レーダー機器（輸出管理重点）──────────────────────────────
    {"agreement_code": "JAEPA",  "hs_code": "8526.91", "hs_description": "無線航法補助機器（GPS等）",
     "year": 2024, "mfn_rate_pct": 2.2, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "RCEP",   "hs_code": "8526.91", "hs_description": "無線航法補助機器（GPS等）",
     "year": 2024, "mfn_rate_pct": 5.0, "preferential_rate_pct": 0.0, "staging_category": "B", "is_eliminated": False, "elimination_year": 2032,
     "notes": "軍民両用品。MTCR対象品は別途輸出許可要件あり。"},
    {"agreement_code": "CPTPP",  "hs_code": "8526.91", "hs_description": "無線航法補助機器（GPS等）",
     "year": 2024, "mfn_rate_pct": 2.2, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "JAEPA",  "hs_code": "8517.62", "hs_description": "無線通信機器（その他）",
     "year": 2024, "mfn_rate_pct": 0.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "RCEP",   "hs_code": "8517.62", "hs_description": "無線通信機器（その他）",
     "year": 2024, "mfn_rate_pct": 0.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    # ── 化学品・毒性物質（CWC関連）──────────────────────────────────────
    {"agreement_code": "JAEPA",  "hs_code": "2901.10", "hs_description": "飽和炭化水素（メタン・エタン・プロパン等）",
     "year": 2024, "mfn_rate_pct": 3.7, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "RCEP",   "hs_code": "2901.10", "hs_description": "飽和炭化水素",
     "year": 2024, "mfn_rate_pct": 2.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "JAEPA",  "hs_code": "2931.90", "hs_description": "有機リン化合物（その他）",
     "year": 2024, "mfn_rate_pct": 5.5, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True,
     "notes": "CWC Schedule 2/3物質を含む場合は別途申告・許可要件あり。"},
    {"agreement_code": "RCEP",   "hs_code": "2931.90", "hs_description": "有機リン化合物（その他）",
     "year": 2024, "mfn_rate_pct": 6.5, "preferential_rate_pct": 2.5, "staging_category": "B", "is_eliminated": False, "elimination_year": 2031},
    # ── 産業機械・ポンプ（輸出管理対象になりうる品目）────────────────
    {"agreement_code": "JAEPA",  "hs_code": "8413.91", "hs_description": "ポンプの部分品（液体用）",
     "year": 2024, "mfn_rate_pct": 1.7, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "RCEP",   "hs_code": "8413.91", "hs_description": "ポンプの部分品（液体用）",
     "year": 2024, "mfn_rate_pct": 5.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "JAEPA",  "hs_code": "8479.89", "hs_description": "機械及び機械的装置（その他）",
     "year": 2024, "mfn_rate_pct": 1.7, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "RCEP",   "hs_code": "8479.89", "hs_description": "機械及び機械的装置（その他）",
     "year": 2024, "mfn_rate_pct": 5.0, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "CPTPP",  "hs_code": "8479.89", "hs_description": "機械及び機械的装置（その他）",
     "year": 2024, "mfn_rate_pct": 1.7, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    # ── 航空宇宙・防衛関連（EAR/ITAR注意品目）──────────────────────────
    {"agreement_code": "JAEPA",  "hs_code": "8801.00", "hs_description": "気球・グライダー・ハンググライダー等",
     "year": 2024, "mfn_rate_pct": 3.7, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "CPTPP",  "hs_code": "8801.00", "hs_description": "気球・グライダー",
     "year": 2024, "mfn_rate_pct": 3.7, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
    {"agreement_code": "JAEPA",  "hs_code": "8802.40", "hs_description": "航空機（自重15,000kg超）",
     "year": 2024, "mfn_rate_pct": 2.7, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True,
     "notes": "EAR 9A991 / ITAR Category VIII対象品は別途許可要件あり。"},
    {"agreement_code": "CPTPP",  "hs_code": "8802.40", "hs_description": "航空機（自重15,000kg超）",
     "year": 2024, "mfn_rate_pct": 2.7, "preferential_rate_pct": 0.0, "staging_category": "A", "is_eliminated": True},
]

_COUNTRY_TO_AGREEMENTS: dict[str, list[str]] = {
    **{c: ["JAEPA"] for c in [
        "AT","BE","BG","HR","CY","CZ","DK","EE","FI","FR","DE","GR",
        "HU","IE","IT","LV","LT","LU","MT","NL","PL","PT","RO","SK","SI","ES","SE"
    ]},
    "CN": ["RCEP"], "KR": ["RCEP"], "AU": ["RCEP","CPTPP"],
    "NZ": ["RCEP","CPTPP"], "VN": ["RCEP","JAVNEPA","CPTPP"],
    "MY": ["RCEP","JAMEPA","CPTPP"], "TH": ["RCEP","JATHFTA"],
    "SG": ["RCEP","JASINGFTA","CPTPP"], "ID": ["RCEP"],
    "PH": ["RCEP"], "BN": ["RCEP","CPTPP"], "KH": ["RCEP"],
    "LA": ["RCEP"], "MM": ["RCEP"],
    "CA": ["CPTPP"], "MX": ["CPTPP"], "CL": ["CPTPP"], "PE": ["CPTPP"],
    "GB": ["UKJFTA","CPTPP"],
    "US": ["USJTA"],
    "IN": ["JAINEPA"],
    "PH": ["RCEP", "JAPHEPA"],
    "ID": ["RCEP", "JAIDFTA"],
    "MN": ["JAMNEPA"],
    "AE": ["JAUAEPA"],
}


# ── ヘルパー ──────────────────────────────────────────────────────

def _serialize_rate(rt: FtaRate | None) -> dict | None:
    if rt is None:
        return None
    return {
        "id": str(rt.id),
        "agreement_code": rt.agreement_code,
        "hs_code": rt.hs_code,
        "hs_description": rt.hs_description,
        "year": rt.year,
        "mfn_rate_pct": rt.mfn_rate_pct,
        "preferential_rate_pct": rt.preferential_rate_pct,
        "staging_category": rt.staging_category,
        "is_eliminated": rt.is_eliminated,
        "elimination_year": rt.elimination_year,
        "notes": rt.notes,
    }


# ── API ────────────────────────────────────────────────────────────

@router.get("/api/fta/agreements")
async def list_agreements(
    origin_country: str | None = Query(None),
    db: AsyncSession = Depends(get_pg_db),
):
    q = select(FtaAgreement).order_by(FtaAgreement.code)
    if origin_country:
        q = q.where(FtaAgreement.origin_country == origin_country.upper())
    r = await db.execute(q)
    return [
        {
            "id": str(a.id),
            "code": a.code,
            "name_ja": a.name_ja,
            "name_en": a.name_en,
            "partner_countries": a.partner_countries.split(","),
            "origin_country": a.origin_country,
            "effective_date": a.effective_date,
            "status": a.status,
            "notes": a.notes,
        }
        for a in r.scalars().all()
    ]


@router.get("/api/fta/rates")
async def list_rates(
    hs_code: str | None = Query(None),
    agreement_code: str | None = Query(None),
    year: int = Query(2024),
    db: AsyncSession = Depends(get_pg_db),
):
    q = select(FtaRate).where(FtaRate.year == year)
    if hs_code:
        q = q.where(FtaRate.hs_code == hs_code.replace(" ", ""))
    if agreement_code:
        q = q.where(FtaRate.agreement_code == agreement_code)
    r = await db.execute(q.order_by(FtaRate.agreement_code))
    return [_serialize_rate(rt) for rt in r.scalars().all()]


@router.get("/api/fta/check")
async def fta_check(
    hs_code: str = Query(...),
    destination_country: str = Query(...),
    origin_country: str = Query("JP"),
    year: int = Query(2024),
    db: AsyncSession = Depends(get_pg_db),
):
    """HS コード × 仕向地国 × 原産国 → 適用可能な特恵税率を一覧返却。"""
    country = destination_country.upper().strip()
    origin = origin_country.upper().strip()
    hs = hs_code.strip()

    if origin == "JP":
        applicable_agreements = _COUNTRY_TO_AGREEMENTS.get(country, [])
    else:
        r = await db.execute(
            select(FtaAgreement.code, FtaAgreement.partner_countries).where(
                FtaAgreement.origin_country == origin,
                FtaAgreement.status == "active",
            )
        )
        applicable_agreements = [
            code for code, partners in r.all()
            if country in (partners or "").split(",")
        ]

    if not applicable_agreements:
        return {
            "hs_code": hs,
            "destination_country": country,
            "origin_country": origin,
            "year": year,
            "applicable_agreements": [],
            "rates": [],
            "summary": f"{origin}→{country} の EPA/FTA は未締結（または未登録）です。MFN 税率が適用されます。",
        }

    r = await db.execute(
        select(FtaRate).where(
            FtaRate.hs_code == hs,
            FtaRate.year == year,
            FtaRate.agreement_code.in_(applicable_agreements),
        )
    )
    rates = r.scalars().all()
    rate_list = [_serialize_rate(rt) for rt in rates]

    best_rate = None
    best_rate_pct = None
    for rt in rates:
        if rt.preferential_rate_pct is not None:
            if best_rate_pct is None or rt.preferential_rate_pct < best_rate_pct:
                best_rate_pct = rt.preferential_rate_pct
                best_rate = rt

    summary_parts = []
    if best_rate:
        saving = (best_rate.mfn_rate_pct or 0) - best_rate_pct
        summary_parts.append(
            f"最優遇税率: {best_rate.agreement_code} {best_rate_pct:.1f}%"
            + (f"（MFN {best_rate.mfn_rate_pct:.1f}% から {saving:.1f}% 削減）" if saving > 0 else "（MFN と同率）")
        )
    elif applicable_agreements:
        summary_parts.append(f"適用可能な協定（{', '.join(applicable_agreements)}）はありますが、当該 HS コードの税率データが未登録です。")

    return {
        "hs_code": hs,
        "destination_country": country,
        "origin_country": origin,
        "year": year,
        "applicable_agreements": applicable_agreements,
        "rates": rate_list,
        "best_rate": _serialize_rate(best_rate) if best_rate else None,
        "summary": " / ".join(summary_parts) if summary_parts else "税率データなし",
    }


@router.get("/api/fta/rules-of-origin")
async def get_rules_of_origin(
    agreement_code: str | None = Query(None),
    hs_code: str | None = Query(None),
):
    """原産地規則一覧を返す（協定コード・HSコードでフィルタ可）。"""
    if agreement_code:
        code = agreement_code.upper()
        if hs_code:
            rules = _get_applicable_rules(code, hs_code)
        else:
            rules = _RULES_OF_ORIGIN.get(code, [])
        return {"agreement_code": code, "rules": rules}

    result = {}
    for code, rules in _RULES_OF_ORIGIN.items():
        result[code] = rules
    return result


@router.post("/api/fta/determine-origin")
async def determine_origin(req: OriginDetermineRequest):
    """製造工程情報をもとに、指定協定での原産資格を判定する。"""
    agreement_code = req.agreement_code.upper()
    applicable_rules = _get_applicable_rules(agreement_code, req.hs_code)

    if not applicable_rules:
        return {
            "qualified": None,
            "reason": f"協定 {agreement_code} の原産地規則データが未登録です。",
            "applicable_rules": [],
            "certificate": None,
        }

    rule = applicable_rules[0]
    rule_type = rule.get("rule_type", "")
    qualified = False
    reason_parts = []

    # ① 完全取得品
    if req.is_wholly_obtained:
        qualified = True
        reason_parts.append("✅ 完全取得品として原産資格あり")
    # ② 付加価値基準
    elif rule_type == "rvc" and req.rvc_pct is not None:
        threshold = rule.get("rvc_threshold") or 40
        if req.rvc_pct >= threshold:
            qualified = True
            reason_parts.append(f"✅ 付加価値基準（RVC）適合: {req.rvc_pct:.1f}% ≥ {threshold}%")
        else:
            reason_parts.append(f"❌ 付加価値基準（RVC）不適合: {req.rvc_pct:.1f}% < {threshold}%（閾値）")
    # ③ 関税番号変更基準
    elif rule_type in ("cc", "ctsh", "cts") and req.tariff_shift_satisfied is not None:
        if req.tariff_shift_satisfied:
            qualified = True
            type_label = {"cc": "CC（4桁変更）", "ctsh": "CTSH（6桁変更）", "cts": "CTS（2桁変更）"}.get(rule_type, rule_type)
            reason_parts.append(f"✅ 関税番号変更基準（{type_label}）適合")
        else:
            reason_parts.append(f"❌ 関税番号変更基準が未確認または不適合")
    # ④ 複合基準（rvc + tariff_shift）
    elif rule_type == "rvc":
        if req.rvc_pct is None and req.tariff_shift_satisfied is None:
            reason_parts.append("⚠ RVC（%）または関税番号変更の入力が必要です")
        elif req.tariff_shift_satisfied:
            qualified = True
            reason_parts.append("✅ 関税番号変更基準（代替）で原産資格あり")
        else:
            reason_parts.append("⚠ 詳細な製造情報を入力してください")
    else:
        reason_parts.append(f"ℹ 規則タイプ「{rule_type}」: 上記の製造情報では判定が不確定です")

    # 証明書情報
    cert_info = _CERTIFICATE_REQUIREMENTS.get(agreement_code)

    return {
        "qualified": qualified,
        "hs_code": req.hs_code,
        "agreement_code": agreement_code,
        "reason": " / ".join(reason_parts),
        "applicable_rules": applicable_rules,
        "certificate": cert_info,
        "next_steps": (
            [
                f"原産地証明書「{cert_info['certificate_name']}」を準備する",
                f"発給機関: {cert_info['issuing_body']}",
                f"必要書類: " + "・".join(cert_info.get("required_documents", [])[:3]),
            ]
            if cert_info and qualified
            else (
                ["製造工程の見直しまたは非原産材料の調達先変更を検討"]
                if not qualified
                else ["原産地規則データを確認し、製造情報を入力してください"]
            )
        ),
    }


@router.get("/api/fta/certificates")
async def get_certificate_requirements(
    agreement_code: str | None = Query(None),
):
    """証明書要件を返す（協定コードでフィルタ可）。"""
    if agreement_code:
        code = agreement_code.upper()
        cert = _CERTIFICATE_REQUIREMENTS.get(code)
        if not cert:
            raise HTTPException(status_code=404, detail=f"協定 {code} の証明書情報が見つかりません")
        return {code: cert}
    return _CERTIFICATE_REQUIREMENTS


@router.post("/api/fta/seed", status_code=201)
async def seed_fta_data(db: AsyncSession = Depends(get_pg_db)):
    """初期シードデータを投入する（既存データは上書きしない）。"""
    added_agreements = 0
    for a_data in _AGREEMENTS_SEED:
        existing = await db.execute(
            select(FtaAgreement).where(FtaAgreement.code == a_data["code"])
        )
        if existing.scalar_one_or_none() is None:
            db.add(FtaAgreement(**a_data, id=uuid.uuid4()))
            added_agreements += 1

    await db.flush()

    added_rates = 0
    for r_data in _RATES_SEED:
        existing = await db.execute(
            select(FtaRate).where(
                FtaRate.agreement_code == r_data["agreement_code"],
                FtaRate.hs_code == r_data["hs_code"],
                FtaRate.year == r_data["year"],
            )
        )
        if existing.scalar_one_or_none() is None:
            db.add(FtaRate(**r_data, id=uuid.uuid4()))
            added_rates += 1

    await db.commit()
    return {"ok": True, "added_agreements": added_agreements, "added_rates": added_rates}


# ── UI ─────────────────────────────────────────────────────────────

@router.get("/ui/fta-check", response_class=HTMLResponse, include_in_schema=False)
async def fta_check_ui(request: Request):
    return templates.TemplateResponse(request, "fta_check.html", {})
