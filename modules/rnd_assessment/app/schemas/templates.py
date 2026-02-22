from __future__ import annotations

from typing import Optional, List, Literal
from pydantic import BaseModel, Field


# =========================================================
# Existing: UseRequirementV1 / EndUserRequirementV1
# =========================================================
class UseRequirementV1(BaseModel):
    schema_version: str = Field(default="use_v1")

    process: Optional[str] = None  # KrF / ArF / EUV / ...
    product_category: Optional[str] = None  # photoresist / ...
    tech_node_nm: Optional[int] = None
    application: Optional[str] = None  # logic / memory / CIS / ...
    rd_phase: Optional[str] = None  # R&D_only / pilot / mass_production
    usage_description: Optional[str] = None

    dual_use_potential: bool = False
    military_end_use_possible: bool = False
    surveillance_end_use_possible: bool = False

    tags: List[str] = Field(default_factory=list)


class EndUserRequirementV1(BaseModel):
    schema_version: str = Field(default="end_user_v1")

    end_user_name: Optional[str] = None
    end_user_country: Optional[str] = None  # JP / US / CN ...
    end_user_type: str = "unknown"  # manufacturer / university / reseller / ...
    intended_countries: List[str] = Field(default_factory=list)

    retransfer_possible: Optional[bool] = None
    retransfer_notes: Optional[str] = None

    restricted_party_screened: Optional[bool] = None
    screening_reference: Optional[str] = None

    notes: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


# =========================================================
# NEW: Disclosure (Photoresist) Template
# =========================================================
StrategicImportance = Literal["low", "medium", "high", "critical"]
RiskLevel3 = Literal["low", "medium", "high"]

# novelty_source 候補（材料向け）
NoveltySource = Literal[
    "polymer_design",
    "pag_design",
    "quencher_design",
    "composition_ratio",
    "synthesis_process",
    "purification_process",
    "formulation_process",
    "processing_conditions",
    "patterning_performance",
    "other",
]


class DisclosureRequirementPhotoresistV1(BaseModel):
    """
    フォトレジスト向け Open/Close 戦略入力テンプレ
    - 研究者が一次入力できる粒度
    - 知財法務がレビューして最終判断に使える粒度
    """
    schema_version: str = Field(default="disclosure_photoresist_v1")

    # 対象
    resist_type: Optional[str] = Field(
        default=None,
        description="KrF / ArF / EUV / i-line / other",
    )
    target_node_nm: Optional[int] = Field(default=None)
    application: Optional[str] = Field(default=None)  # logic / memory / CIS / ...

    # 新規性の源泉（複数選択）
    novelty_source: List[NoveltySource] = Field(default_factory=list)

    # 再現性・逆解析
    reproducibility_risk: Optional[RiskLevel3] = Field(default=None)
    reverse_engineering_risk: Optional[RiskLevel3] = Field(default=None)
    requires_process_knowhow: Optional[bool] = Field(default=None)

    # 戦略・安全保障
    strategic_importance: Optional[StrategicImportance] = Field(default=None)
    export_control_sensitivity: Optional[bool] = Field(default=None)
    military_dual_use_potential: Optional[bool] = Field(default=None)

    # 公開・特許
    planned_patent_filing: Optional[bool] = Field(default=None)
    planned_filing_region: List[str] = Field(default_factory=list)  # JP / US / EU / CN / ...
    publication_intent: Optional[bool] = Field(default=None)

    # 外部関係
    external_collaboration: Optional[bool] = Field(default=None)
    partner_countries: List[str] = Field(default_factory=list)
    nda_in_place: Optional[bool] = Field(default=None)

    notes: Optional[str] = Field(default=None)