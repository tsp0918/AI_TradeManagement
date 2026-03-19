"""
platform_core.ontology.models.regulation
==========================================
規制オントロジーの核心Pydanticモデル群。

HanteiKubanbang が「判定に必要なパラメータ」を構造的に保持し、
OntologyReasoningEngine の `required - known = missing` 計算の源泉となる。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional, Union

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────
# 列挙型
# ─────────────────────────────────────────────────

class ComparisonOperator(str, Enum):
    GTE    = ">="
    GT     = ">"
    LTE    = "<="
    LT     = "<"
    EQ     = "=="
    IN     = "in"      # value が controlled_values リストに含まれる
    NOT_IN = "not_in"  # value が excluded_values リストに含まれない


class ConcernLevel(str, Enum):
    HIGH   = "high"
    MEDIUM = "medium"
    LOW    = "low"


# ─────────────────────────────────────────────────
# Threshold（数値閾値）
# ─────────────────────────────────────────────────

class Threshold(BaseModel):
    """
    規制項番の判定に必要な技術パラメータの閾値定義。
    「この属性値がこの条件を満たせば規制対象（または除外）」を表現する。

    例:
        operating_frequency_ghz >= 3.0 → 7項 規制対象
        bias_stability_deg_h    >= 0.005 → 2-7項 除外（非該当）
    """
    attr_key:          str            # 属性識別子  e.g. "operating_frequency_ghz"
    label:             str            # 表示名       e.g. "動作周波数"
    unit:              Optional[str]  # 単位         e.g. "GHz"
    operator:          ComparisonOperator
    controlled_value:  float          # 規制閾値
    description:       str  = ""
    priority:          int  = 50      # 高いほど先に確認（100が最高）
    example:           Optional[str] = None  # 回答例 e.g. "3.5"

    def is_controlled(self, actual_value: float) -> bool:
        """閾値判定：actual_value が規制条件を満たすか"""
        ops: dict[ComparisonOperator, Any] = {
            ComparisonOperator.GTE: lambda a, t: a >= t,
            ComparisonOperator.GT:  lambda a, t: a >  t,
            ComparisonOperator.LTE: lambda a, t: a <= t,
            ComparisonOperator.LT:  lambda a, t: a <  t,
            ComparisonOperator.EQ:  lambda a, t: a == t,
        }
        fn = ops.get(self.operator)
        return fn(actual_value, self.controlled_value) if fn else False


# ─────────────────────────────────────────────────
# NonNumericAttribute（非数値属性）
# ─────────────────────────────────────────────────

class NonNumericAttribute(BaseModel):
    """
    数値閾値で表現できない属性の定義。
    用途種別・需要者種別・仕向国など、カテゴリ値で判定するもの。

    controlled_values が空の場合は「値を確認するだけ」（排除条件なし）。
    """
    attr_key:           str
    label:              str
    description:        str       = ""
    priority:           int       = 50
    example:            Optional[str] = None
    controlled_values:  list[str] = Field(default_factory=list)
    excluded_values:    list[str] = Field(default_factory=list)

    def is_controlled(self, actual_value: str) -> Optional[bool]:
        """
        controlled_values / excluded_values で判定。
        None = 判定不能（どちらにも該当しない場合）。
        """
        if self.excluded_values and actual_value in self.excluded_values:
            return False
        if self.controlled_values and actual_value in self.controlled_values:
            return True
        return None


# ─────────────────────────────────────────────────
# HanteiKubanbang（判定項番）
# ─────────────────────────────────────────────────

class HanteiKubanbang(BaseModel):
    """
    オントロジーの核心クラス。
    「この規制項番を判定するために必要な情報は何か」を構造的に定義する。

    FAISS Layer A の 1グループ（item_no + source_type + article_no）に対応するが、
    「判定パラメータ・閾値」という付加知識を持つ点が異なる。

    ★ required_attr_keys() が `required - known = missing` の「required」を提供する。
    """
    # 識別子（Layer A のグルーピングキーと対応）
    item_no:     str  = ""  # e.g. "7", "外為令6", "" (ECCNの場合)
    source_type: str  = ""  # "law" | "parameter" | "tsutatsu" | "eccn"
    article_no:  str  = ""  # e.g. "6", "18"
    eccn:        str  = ""  # e.g. "3A001a" (source_type="eccn" の場合)

    # 表示情報
    title:         str  = ""   # 正式表記  e.g. "輸出令第7項 貨物等省令第6条（電子部品・集積回路・半導体）"
    short_label:   str  = ""   # 短縮表記  e.g. "7項 電子部品"
    concern_level: ConcernLevel = ConcernLevel.MEDIUM

    # FAISS連携
    layer_a_faiss_ids: list[int] = Field(default_factory=list)

    # ★ 判定に必要なパラメータ（質問戦略の源泉）
    thresholds:         list[Threshold]          = Field(default_factory=list)
    non_numeric_attrs:  list[NonNumericAttribute] = Field(default_factory=list)

    @property
    def domain_id(self) -> str:
        """オントロジー内の一意識別子"""
        if self.source_type == "eccn":
            return f"ECCN::{self.eccn}"
        return f"FEFTA::{self.item_no}::{self.source_type}::{self.article_no}"

    def get_required_attr_keys(self) -> set[str]:
        """★ 判定に必要な全属性キーの集合（required の源泉）"""
        keys: set[str] = set()
        keys.update(t.attr_key for t in self.thresholds)
        keys.update(a.attr_key for a in self.non_numeric_attrs)
        return keys

    def get_all_attributes(self) -> list[Union[Threshold, NonNumericAttribute]]:
        """Threshold + NonNumericAttribute を優先度降順で返す"""
        all_attrs: list[Union[Threshold, NonNumericAttribute]] = (
            list(self.thresholds) + list(self.non_numeric_attrs)
        )
        return sorted(all_attrs, key=lambda a: -a.priority)

    def apply_threshold(
        self, attr_key: str, value: Any
    ) -> Optional[bool]:
        """
        特定属性の値を受け取り、規制判定を返す。
        True  = 規制対象
        False = 除外（非該当）
        None  = 判定不能（情報不足 or 閾値なし）
        """
        for t in self.thresholds:
            if t.attr_key == attr_key:
                try:
                    return t.is_controlled(float(value))
                except (TypeError, ValueError):
                    return None
        for a in self.non_numeric_attrs:
            if a.attr_key == attr_key:
                return a.is_controlled(str(value))
        return None
