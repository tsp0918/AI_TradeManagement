"""
platform_core.ontology.models.catchall
=======================================
キャッチオール規制（輸出令第4条・第4条の2）に係るデータモデル。

Phase 1 対応範囲:
  - 大量破壊兵器等キャッチオール（輸出令第4条）: 全仕向地が対象
  - 通常兵器キャッチオール（輸出令第4条の2）: 輸出令別表第3掲載国（懸念国）向けが対象

Phase 2 追加:
  - FeftaStatus: ホワイト国（別表第3の2）／懸念国／一般国 の区分
  - ControlReasons: EAR Country Chart 13列（NS/MT/NP/CB/RS/AT/FC）
  - CatchallJudgment: ear_groups / control_reasons / fefta_status フィールド追加

用語:
  - インフォーム通知: 経産省から輸出者に対し特定取引について許可申請を要求する通知
  - Red Flag: 不審な取引を示す7つのインジケーター（METI ガイダンス準拠）
  - 自己判定: 輸出者自身がキャッチオール該当性を判断するプロセス
  - EAR Country Chart: 米国商務省 BIS 15 CFR Part 738 Supplement 1 の輸出管理対象国分類表
  - ホワイト国: 輸出令別表第3の2 掲載国（信頼性の高い輸出管理実施国）
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# 列挙型
# ──────────────────────────────────────────────

class CatchallType(str, Enum):
    """キャッチオール種別"""
    WMD          = "wmd"           # 大量破壊兵器等キャッチオール（第4条）
    CONVENTIONAL = "conventional"  # 通常兵器キャッチオール（第4条の2）


class CatchallVerdict(str, Enum):
    """キャッチオール自己判定結果"""
    REQUIRES_PERMIT = "requires_permit"   # 輸出許可申請が必要
    CAUTION         = "caution"           # 許可不要だが要注意（モニタリング継続）
    CLEAR           = "clear"             # キャッチオール非該当（現時点）
    INDETERMINATE   = "indeterminate"     # 情報不足により判定不能


class CountryRiskLevel(str, Enum):
    """仕向地リスクレベル"""
    HIGH      = "HIGH"
    MEDIUM    = "MEDIUM"
    LOW       = "LOW"
    WHITELIST = "WHITELIST"  # 輸出令別表第3の2掲載（ホワイト国）


class FeftaStatus(str, Enum):
    """外為法上の仕向地ステータス"""
    CONCERN   = "concern"    # 輸出令別表第3掲載（懸念国）
    WHITELIST = "whitelist"  # 輸出令別表第3の2掲載（ホワイト国）
    GENERAL   = "general"    # 一般国


class ControlReasons(BaseModel):
    """EAR Country Chart（15 CFR Part 738 Supplement 1）主要制御列フラグ"""
    ns1: bool = False   # National Security Col.1 — Wassenaar List 対象品目
    ns2: bool = False   # National Security Col.2 — EAR99 相当含む汎用品
    mt:  bool = False   # Missile Technology     — MTCR Annex 対象品目
    np1: bool = False   # Nuclear Nonprolif. Col.1 — NSG Trigger List（核特定品目）
    np2: bool = False   # Nuclear Nonprolif. Col.2 — NSG Dual-Use（核関連汎用品）
    cb1: bool = False   # Chem & Bio Col.1       — Australia Group Schedule 1/2
    cb2: bool = False   # Chem & Bio Col.2       — Australia Group Schedule 3
    cb3: bool = False   # Chem & Bio Col.3       — 化学・生物関連汎用品
    rs1: bool = False   # Regional Stability Col.1 — 兵器・軍用品
    rs2: bool = False   # Regional Stability Col.2 — 汎用品
    at1: bool = False   # Anti-Terrorism Col.1   — E:1 テロ支援国家指定
    at2: bool = False   # Anti-Terrorism Col.2   — 軍関連品（非E:1）
    fc1: bool = False   # Firearms Convention    — OAS 銃器条約締約国

    def active_columns(self) -> list[str]:
        """True の列キーを返す"""
        return [k for k, v in self.model_dump().items() if v]


# ──────────────────────────────────────────────
# Red Flag 7項目（METI ガイダンス準拠）
# ──────────────────────────────────────────────

class RedFlagAnswers(BaseModel):
    """
    Red Flag チェックリスト（Y/N + 補足メモ）。
    True = 当該フラグが該当する（懸念あり）。
    None = 未確認（デフォルト）。
    """
    # RF-1: 取引相手・エンドユーザーの身元が不明・確認困難
    rf1_unknown_end_user: Optional[bool] = None
    rf1_note: Optional[str] = None

    # RF-2: 最終用途・最終需要者の説明が不自然・拒否・回避
    rf2_suspicious_end_use: Optional[bool] = None
    rf2_note: Optional[str] = None

    # RF-3: 通常の取引と比較して価格設定が不自然（過高・過低）
    rf3_abnormal_price: Optional[bool] = None
    rf3_note: Optional[str] = None

    # RF-4: 支払い方法が不自然（現金・第三者経由・タックスヘイブン等）
    rf4_unusual_payment: Optional[bool] = None
    rf4_note: Optional[str] = None

    # RF-5: 輸送・包装・経由地の指定が不自然（懸念国経由、迂回ルート等）
    rf5_suspicious_routing: Optional[bool] = None
    rf5_note: Optional[str] = None

    # RF-6: 品目の用途・技術的特性に関する知識が乏しい、または質問を回避
    rf6_no_technical_knowledge: Optional[bool] = None
    rf6_note: Optional[str] = None

    # RF-7: 懸念国または懸念地域の中継業者・転売業者を経由する取引
    rf7_concern_country_routing: Optional[bool] = None
    rf7_note: Optional[str] = None

    def positive_flags(self) -> list[str]:
        """該当（True）のフラグキーを返す"""
        result = []
        flag_keys = [
            "rf1_unknown_end_user", "rf2_suspicious_end_use",
            "rf3_abnormal_price", "rf4_unusual_payment",
            "rf5_suspicious_routing", "rf6_no_technical_knowledge",
            "rf7_concern_country_routing",
        ]
        for k in flag_keys:
            if getattr(self, k) is True:
                result.append(k)
        return result

    def answered_count(self) -> int:
        """回答済み（True/False）の項目数を返す"""
        count = 0
        flag_keys = [
            "rf1_unknown_end_user", "rf2_suspicious_end_use",
            "rf3_abnormal_price", "rf4_unusual_payment",
            "rf5_suspicious_routing", "rf6_no_technical_knowledge",
            "rf7_concern_country_routing",
        ]
        for k in flag_keys:
            if getattr(self, k) is not None:
                count += 1
        return count


# ──────────────────────────────────────────────
# 入力コンテキスト
# ──────────────────────────────────────────────

class CatchallContext(BaseModel):
    """
    キャッチオール自己判定に必要な入力情報。

    ai_validation の transaction_id と紐付けて使用する。
    two_lists の結果が LOW（リスト規制の候補なし）の場合に評価を実行する。
    """
    transaction_id:     int
    session_id:         str

    # ── 仕向地情報 ──────────────────────────────────────────────────────────
    destination_country: Optional[str] = None    # ISO alpha-2 (e.g. "CN", "KP")
    consignee_name:      Optional[str] = None    # 荷受人名
    end_user_name:       Optional[str] = None    # 最終需要者名
    end_user_country:    Optional[str] = None    # 最終需要者の所在国（ISO alpha-2）

    # ── インフォーム通知 ─────────────────────────────────────────────────────
    inform_received:     Optional[bool] = None   # 経産省からのインフォーム通知を受領したか
    inform_note:         Optional[str]  = None

    # ── 最終用途申告 ─────────────────────────────────────────────────────────
    end_use_description: Optional[str] = None    # 最終用途の具体的説明
    euc_obtained:        Optional[bool] = None   # 最終需要者証明書（EUC）を取得したか

    # ── Red Flag チェックリスト ───────────────────────────────────────────────
    red_flags:           RedFlagAnswers = Field(default_factory=RedFlagAnswers)

    # ── メタ ─────────────────────────────────────────────────────────────────
    evaluated_at:        datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    evaluator_note:      Optional[str] = None    # 担当者メモ


# ──────────────────────────────────────────────
# 出力（判定結果）
# ──────────────────────────────────────────────

class CatchallJudgmentReason(BaseModel):
    """個別判断根拠"""
    code:    str   # e.g. "INFORM_RECEIVED", "RED_FLAG_RF1", "CONCERN_COUNTRY_HIGH"
    label:   str
    detail:  str


class CatchallJudgment(BaseModel):
    """
    キャッチオール自己判定の出力。

    verdict が REQUIRES_PERMIT の場合は輸出許可申請が必要。
    CAUTION の場合はモニタリングを継続し記録を保管する。
    """
    transaction_id:     int
    session_id:         str
    evaluated_at:       datetime

    # ── 適用されるキャッチオール種別 ─────────────────────────────────────────
    applicable_types:   list[CatchallType] = Field(default_factory=list)

    # ── 仕向地リスク ─────────────────────────────────────────────────────────
    destination_country:      Optional[str] = None
    destination_risk_level:   Optional[CountryRiskLevel] = None
    destination_country_name: Optional[str] = None
    is_concern_country:       bool = False
    is_whitelist_country:     bool = False   # ホワイト国（通常兵器キャッチオール除外）

    # ── EAR 国別分類情報（Phase 2 追加） ─────────────────────────────────────
    fefta_status:        Optional[str] = None          # "concern"|"whitelist"|"general"
    ear_groups:          list[str] = Field(default_factory=list)  # ["D:1","D:3",...]
    ear_is_embargoed:    bool = False                  # E:1 テロ支援国家指定
    control_reasons:     Optional[Dict[str, bool]] = None  # EAR Country Chart 列フラグ
    whitelist_agreements: list[str] = Field(default_factory=list)  # 多国間体制参加リスト

    # ── Red Flag サマリー ─────────────────────────────────────────────────────
    red_flag_positive_count:  int = 0
    red_flag_keys:            list[str] = Field(default_factory=list)

    # ── 総合判定 ──────────────────────────────────────────────────────────────
    verdict:            CatchallVerdict = CatchallVerdict.INDETERMINATE
    verdict_label:      str = "判定不能"
    verdict_detail:     str = ""

    # ── 判断根拠リスト ────────────────────────────────────────────────────────
    reasons:            list[CatchallJudgmentReason] = Field(default_factory=list)

    # ── 推奨アクション ────────────────────────────────────────────────────────
    recommended_actions: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
