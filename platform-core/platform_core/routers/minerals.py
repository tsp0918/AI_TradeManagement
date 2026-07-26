"""重要鉱物スキャナー — POST /api/minerals/scan

製品の BOM（部品構成表）を受け取り、重要鉱物リスク・FEOC曝露・
中国輸出規制リスク・CRMA 2030 影響・推奨事項を返す。
"""

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(tags=["minerals"])

# ── 重要鉱物データ読み込み ────────────────────────────────────────────
_SEED_DIR = Path(__file__).resolve().parents[3] / "platform_core" / "ontology" / "seed"
_CRITICAL_MINERALS_PATH = _SEED_DIR / "critical_minerals.json"
_CRITICAL_MINERALS: dict = {}

def _load_critical_minerals() -> dict:
    global _CRITICAL_MINERALS
    if _CRITICAL_MINERALS:
        return _CRITICAL_MINERALS
    # フォールバックパス
    fallback_paths = [
        _CRITICAL_MINERALS_PATH,
        Path(__file__).resolve().parents[4] / "data" / "staging" / "critical_minerals.json",
    ]
    for p in fallback_paths:
        if p.exists():
            try:
                _CRITICAL_MINERALS = json.loads(p.read_text(encoding="utf-8"))
                logger.info("Loaded critical_minerals.json from %s", p)
                return _CRITICAL_MINERALS
            except Exception as e:
                logger.warning("Failed to load %s: %s", p, e)
    logger.warning("critical_minerals.json not found")
    return {}


# ── 静的リスト ─────────────────────────────────────────────────────────
# FEOC関連の高リスク国（IRA §30D 規則より）
_FEOC_COUNTRIES = {"CN", "RU", "KP", "IR"}

# 中国輸出規制品目（2023年施行）
_CN_EXPORT_CTRL_MATERIALS = {
    "gallium", "germanium", "graphite", "ガリウム", "ゲルマニウム", "グラファイト", "黒鉛",
}

# EU CRMA 戦略的原材料（SRM）リスト
_CRMA_SRM = {
    "lithium", "cobalt", "nickel", "manganese", "graphite", "silicon", "boron", "germanium",
    "gallium", "indium", "magnesium", "titanium", "tungsten", "niobium", "phosphorus",
    "リチウム", "コバルト", "ニッケル", "マンガン", "黒鉛", "グラファイト", "シリコン",
    "ガリウム", "ゲルマニウム", "インジウム", "マグネシウム", "チタン", "タングステン",
}

# UFLPA 対象の HS コードプレフィックス（新疆産が疑われる品目）
_UFLPA_HS_PREFIXES = {"6109", "6203", "6204", "2804", "2606", "2615", "8818", "0702"}


# ── Pydantic モデル ────────────────────────────────────────────────────
class BomItem(BaseModel):
    material: str
    origin_country: str
    hs_code: Optional[str] = None
    quantity_kg: Optional[float] = None


class MineralScanRequest(BaseModel):
    product_code: Optional[str] = None
    product_name: Optional[str] = None
    bom_items: list[BomItem] = []


class MineralRisk(BaseModel):
    material: str
    risk_type: str
    severity: str       # "danger" | "warn" | "info"
    detail: str
    recommendation: str


class MineralScanResponse(BaseModel):
    product_code: Optional[str]
    critical_mineral_risks: list[MineralRisk]
    feoc_exposure: list[str]
    crma_2030_impact: list[str]
    china_export_ctrl_risk: list[str]
    uflpa_risk: list[str]
    recommendations: list[str]
    risk_summary: str


# ── エンドポイント ────────────────────────────────────────────────────
@router.post("/api/minerals/scan", response_model=MineralScanResponse)
def scan_minerals(req: MineralScanRequest) -> MineralScanResponse:
    """
    製品 BOM を受け取り、重要鉱物リスクを多面的に評価する。

    評価軸:
    - FEOC 曝露 (IRA §30D / CHIPS ガードレール)
    - 中国輸出規制品目 (Ga/Ge/黒鉛)
    - EU CRMA 2030 調達目標への影響
    - UFLPA 対象 HS コード
    """
    risks: list[MineralRisk] = []
    feoc_exposure: list[str] = []
    crma_impact: list[str] = []
    china_ctrl: list[str] = []
    uflpa_risk: list[str] = []

    for item in req.bom_items:
        mat = item.material.lower()
        country = item.origin_country.upper()[:2]
        hs = (item.hs_code or "").replace(".", "").replace(" ", "")[:4]

        # FEOC 曝露チェック
        if country in _FEOC_COUNTRIES:
            feoc_exposure.append(
                f"{item.material}（原産国: {item.origin_country}）"
            )
            risks.append(MineralRisk(
                material=item.material,
                risk_type="feoc_exposure",
                severity="danger",
                detail=(
                    f"{item.origin_country} は FEOC（懸念外国エンティティ）高リスク国です。"
                    "IRA §30D および CHIPS ガードレール条項の下で補助金・税額控除が失格となる可能性があります。"
                ),
                recommendation="代替調達先（カナダ・オーストラリア・チリ等の FTA 締約国）を早急に検討してください。",
            ))

        # 中国輸出規制品目チェック
        if any(cn_mat in mat for cn_mat in _CN_EXPORT_CTRL_MATERIALS):
            if country == "CN":
                china_ctrl.append(f"{item.material}（中国産）")
                risks.append(MineralRisk(
                    material=item.material,
                    risk_type="china_export_ctrl",
                    severity="danger",
                    detail=(
                        f"{item.material} は中国の輸出管理規制（2023年8月施行）の対象です。"
                        "MOFCOMへの輸出許可申請が必要であり、用途・最終ユーザー情報の開示が求められます。"
                    ),
                    recommendation="代替調達先（ベルギー・カナダ・韓国等）への切り替えと在庫バッファー確保を推奨します。",
                ))

        # EU CRMA SRM チェック
        if any(srm in mat for srm in _CRMA_SRM):
            crma_impact.append(f"{item.material}")
            risks.append(MineralRisk(
                material=item.material,
                risk_type="crma_srm",
                severity="warn",
                detail=(
                    f"{item.material} は EU CRMA の「戦略的原材料（SRM）」に該当する可能性があります。"
                    "EU顧客から調達先多様化・サプライチェーン透明性の開示要求が高まります。"
                ),
                recommendation="CRMA 2030 目標（加工40%EU域内）に向けた調達先の地理的分散を計画してください。",
            ))

        # UFLPA HS コードチェック
        if hs in _UFLPA_HS_PREFIXES:
            uflpa_risk.append(f"{item.material}（HS: {hs}）")
            risks.append(MineralRisk(
                material=item.material,
                risk_type="uflpa_hs",
                severity="warn",
                detail=(
                    f"HS コード {hs} は UFLPA（ウイグル強制労働防止法）の監視対象品目です。"
                    "米国向け輸出では CBP による輸入差し止めリスクがあります。"
                ),
                recommendation="新疆ウイグル自治区（XUAR）との調達接続性を調査し、原産地証明を取得してください。",
            ))

    # 推奨事項まとめ
    recommendations: list[str] = []
    if feoc_exposure:
        recommendations.append(
            f"FEOC 高リスク素材 {len(feoc_exposure)} 件について代替調達先の選定を急いでください（IRA §30D・CHIPS ガードレール対応）。"
        )
    if china_ctrl:
        recommendations.append(
            f"中国輸出規制対象素材 {len(china_ctrl)} 件について輸出許可の取得手続きと在庫リスク評価を実施してください。"
        )
    if crma_impact:
        recommendations.append(
            f"EU CRMA 戦略的原材料 {len(crma_impact)} 種について、2030年調達目標に向けた多様化計画を策定してください。"
        )
    if uflpa_risk:
        recommendations.append(
            f"UFLPA 監視対象品目 {len(uflpa_risk)} 件について、XUAR関連性のサプライヤーアンケートを実施してください。"
        )
    if not recommendations:
        recommendations.append("現時点で重大な重要鉱物リスクは検出されませんでした。定期的な調達先レビューを継続してください。")

    # リスクサマリー
    danger_count = sum(1 for r in risks if r.severity == "danger")
    warn_count   = sum(1 for r in risks if r.severity == "warn")
    if danger_count > 0:
        risk_summary = f"重大リスク {danger_count} 件・警告 {warn_count} 件 — 即時対応が必要です。"
    elif warn_count > 0:
        risk_summary = f"警告 {warn_count} 件 — サプライチェーン多様化の計画を開始してください。"
    else:
        risk_summary = "リスクなし — 現在の調達構成は主要な重要鉱物規制の要件を満たしています。"

    return MineralScanResponse(
        product_code=req.product_code,
        critical_mineral_risks=risks,
        feoc_exposure=feoc_exposure,
        crma_2030_impact=crma_impact,
        china_export_ctrl_risk=china_ctrl,
        uflpa_risk=uflpa_risk,
        recommendations=recommendations,
        risk_summary=risk_summary,
    )
