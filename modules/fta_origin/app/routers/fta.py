"""EPA/FTA 特恵税率 API ルーター。

エンドポイント:
  GET  /api/fta/agreements              協定一覧
  GET  /api/fta/rates                   税率検索（hs_code × agreement_code）
  GET  /api/fta/check                   HS コードと仕向地国から適用可能な特恵税率を返す
  POST /api/fta/seed                    初期データ投入（管理者用）
  GET  /ui/fta-check                    UI ページ
"""

import pathlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..pg_session import get_pg_db
from platform_core.models.fta import FtaAgreement, FtaRate

_TEMPLATES_DIR = pathlib.Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(tags=["fta"])

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
