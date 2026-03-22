"""
platform_core.ontology.models.transaction
==========================================
エージェントが対話を通じて蓄積する取引文脈モデル。

TransactionContext は「現在何が判明しているか（known）」を保持し、
OntologyReasoningEngine が `required - known = missing` を計算するための
「known」側を提供する。
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class EndUseType(str, Enum):
    """最終用途の分類"""
    CIVILIAN        = "civilian"         # 民生用
    MILITARY        = "military"         # 軍事用
    DUAL_USE        = "dual_use"         # デュアルユース
    RESEARCH        = "research"         # 研究・開発
    GOVERNMENT      = "government"       # 政府・官公庁
    UNKNOWN         = "unknown"


class EndUserType(str, Enum):
    """需要者（最終需要者）の種別"""
    COMMERCIAL      = "commercial"       # 民間企業
    MILITARY_ORG    = "military_org"     # 軍・軍関連機関
    GOVERNMENT      = "government"       # 政府機関
    RESEARCH_INST   = "research_inst"    # 研究機関
    INDIVIDUAL      = "individual"       # 個人
    UNKNOWN         = "unknown"


class ConcernFlag(str, Enum):
    """懸念フラグ"""
    NONE            = "none"
    DIVERSION_RISK  = "diversion_risk"   # 転用リスク
    SANCTIONED      = "sanctioned"       # 制裁対象
    WMD_RELATED     = "wmd_related"      # WMD関連
    MILITARY_ORG    = "military_org"     # 軍・軍関連需要者


class DialogueTurn(BaseModel):
    """対話の1ターン"""
    turn: int
    attr_key:   Optional[str]  = None
    question:   str
    answer:     Optional[str]  = None
    timestamp:  datetime       = Field(default_factory=lambda: datetime.now(timezone.utc))


class TransactionContext(BaseModel):
    """
    エージェントが対話を通じて蓄積する取引文脈。

    session_id で識別され、複数ターンにわたって known_attributes を更新し続ける。
    OntologyReasoningEngine が「何が不明か」を計算するための「known」を提供する。
    """
    session_id:         str
    transaction_id:     Optional[int]  = None  # ai_validation の Transaction.id
    initial_query:      str            = ""

    # ── 既知属性（ユーザー回答 or 自動取得で蓄積）──────────────────────────
    known_attributes:   dict[str, Any] = Field(default_factory=dict)

    # ── 高頻度属性（専用フィールド）────────────────────────────────────────
    end_use_type:       EndUseType     = EndUseType.UNKNOWN
    end_user_type:      EndUserType    = EndUserType.UNKNOWN
    destination_country: Optional[str] = None  # ISO alpha-2  e.g. "CN", "KP"
    concern_flags:      list[ConcernFlag] = Field(default_factory=list)

    # ── 候補項番 ────────────────────────────────────────────────────────────
    candidate_domain_ids: list[str]   = Field(default_factory=list)

    # ── 対話履歴 ────────────────────────────────────────────────────────────
    dialogue_history:   list[DialogueTurn] = Field(default_factory=list)

    def get_known_attributes(self) -> dict[str, Any]:
        """
        OntologyReasoningEngine が参照する「既知属性の辞書」を返す。
        専用フィールドも統合して返す。
        """
        base: dict[str, Any] = dict(self.known_attributes)
        if self.end_use_type != EndUseType.UNKNOWN:
            base["end_use_type"] = self.end_use_type.value
        if self.end_user_type != EndUserType.UNKNOWN:
            base["end_user_type"] = self.end_user_type.value
        if self.destination_country:
            base["destination_country"] = self.destination_country
        return base

    def update_attribute(self, attr_key: str, value: Any) -> None:
        """回答を受け取り Context を更新する"""
        if attr_key == "end_use_type":
            try:
                self.end_use_type = EndUseType(value)
            except ValueError:
                pass
        elif attr_key == "end_user_type":
            try:
                self.end_user_type = EndUserType(value)
            except ValueError:
                pass
        elif attr_key == "destination_country":
            self.destination_country = str(value)
        else:
            self.known_attributes[attr_key] = value

    def add_dialogue_turn(
        self, question: str, answer: Optional[str] = None, attr_key: Optional[str] = None
    ) -> None:
        turn = DialogueTurn(
            turn=len(self.dialogue_history) + 1,
            attr_key=attr_key,
            question=question,
            answer=answer,
        )
        self.dialogue_history.append(turn)

    def is_complete(self) -> bool:
        """
        判定に十分な情報が揃ったか（エージェントが判定フェーズへ移行する条件）。
        基本: 候補が1件以下 or 対話ターン数が上限に達した場合。
        空リスト（未設定）は「まだ候補が投入されていない」ため False を返す。
        """
        if not self.candidate_domain_ids:  # 未初期化（セッション開始前）
            return False
        if len(self.candidate_domain_ids) <= 1:
            return True
        if len(self.dialogue_history) >= 10:
            return True
        return False

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")
