"""
platform_core.ontology.models.judgment
=========================================
判定結果モデル。OntologyReasoningEngine が生成し、DBに保存・レポートに変換される。
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class JudgmentStatus(str, Enum):
    CONTROLLED     = "controlled"      # 規制対象（該当）
    NOT_CONTROLLED = "not_controlled"  # 非該当
    REQUIRES_REVIEW = "requires_review"  # 要確認（情報不足）
    PENDING        = "pending"         # 未判定


class JudgmentReason(BaseModel):
    """判定根拠の1件（監査証跡用）"""
    domain_id:    str              # HanteiKubanbang.domain_id
    title:        str              # 規制項番の正式表記
    status:       JudgmentStatus
    attr_key:     Optional[str]   = None   # 判定に使った属性
    attr_value:   Optional[Any]   = None   # その属性値
    threshold_op: Optional[str]   = None   # 閾値表現 e.g. ">= 3.0 GHz"
    explanation:  str             = ""


class JudgmentResult(BaseModel):
    """
    OntologyReasoningEngine が返す最終判定結果。

    reasons に判定根拠を完全記録することで、「なぜその判定か」を説明できる。
    これがFAISS単体との最大の差異点（説明可能性・監査対応）。
    """
    session_id:      str
    transaction_id:  Optional[int]        = None
    overall_status:  JudgmentStatus       = JudgmentStatus.PENDING
    controlled_items: list[str]           = Field(default_factory=list)  # domain_id list
    excluded_items:  list[str]            = Field(default_factory=list)
    pending_items:   list[str]            = Field(default_factory=list)
    reasons:         list[JudgmentReason] = Field(default_factory=list)
    summary:         str                  = ""  # Sonnet生成の自然言語サマリー
    judged_at:       datetime             = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def add_reason(self, reason: JudgmentReason) -> None:
        self.reasons.append(reason)
        if reason.status == JudgmentStatus.CONTROLLED:
            if reason.domain_id not in self.controlled_items:
                self.controlled_items.append(reason.domain_id)
        elif reason.status == JudgmentStatus.NOT_CONTROLLED:
            if reason.domain_id not in self.excluded_items:
                self.excluded_items.append(reason.domain_id)
        else:
            if reason.domain_id not in self.pending_items:
                self.pending_items.append(reason.domain_id)

    def finalize(self) -> None:
        """overall_status を reasons から決定する"""
        if self.controlled_items:
            self.overall_status = JudgmentStatus.CONTROLLED
        elif self.pending_items:
            self.overall_status = JudgmentStatus.REQUIRES_REVIEW
        else:
            self.overall_status = JudgmentStatus.NOT_CONTROLLED

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")
