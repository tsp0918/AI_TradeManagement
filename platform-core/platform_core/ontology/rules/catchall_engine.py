"""
platform_core.ontology.rules.catchall_engine
=============================================
キャッチオール規制（輸出令第4条・第4条の2）自己判定エンジン。

判定フロー:
  1. インフォーム通知受領チェック → 即時 REQUIRES_PERMIT
  1.5. E:1 禁輸国（テロ支援国家）チェック → 即時 REQUIRES_PERMIT
  2. 仕向地チェック（輸出令別表第3 懸念国リスト / 別表第3の2 ホワイト国）
  3. EAR Country Chart 制御列チェック（エビデンス強化）
  4. Red Flag 7項目チェック
  5. EUC 取得状況チェック
  6. 総合判定（Symbolic ルールベース）

適用条件（呼び出し側が担保すること）:
  - FAISS Two-List の結果が LOW（リスト規制候補なし）の場合に呼び出す
  - リスト規制品（輸出令別表第1対象）は別途 HanteiAgent で判定済みであること

根拠条文:
  - 輸出令第4条: 大量破壊兵器等キャッチオール（全仕向地）
  - 輸出令第4条の2: 通常兵器キャッチオール（別表第3 懸念国向け）
  - METI「安全保障貿易管理ガイダンス」(最新版)
  - 米国 BIS EAR Country Groups (15 CFR Part 740 Supplement 1)
  - 米国 BIS EAR Country Chart (15 CFR Part 738 Supplement 1)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from platform_core.ontology.models.catchall import (
    CatchallContext,
    CatchallJudgment,
    CatchallJudgmentReason,
    CatchallType,
    CatchallVerdict,
    CountryRiskLevel,
    FeftaStatus,
)

logger = logging.getLogger(__name__)

_SEED_PATH = Path(__file__).parent.parent / "seed" / "catchall_concern_countries.json"

# Red Flag ラベルマップ（RF キー → 日本語ラベル）
_RED_FLAG_LABELS: dict[str, str] = {
    "rf1_unknown_end_user":        "RF-1: エンドユーザーの身元が不明・確認困難",
    "rf2_suspicious_end_use":      "RF-2: 最終用途の説明が不自然・拒否",
    "rf3_abnormal_price":          "RF-3: 価格設定が不自然（過高・過低）",
    "rf4_unusual_payment":         "RF-4: 支払い方法が不自然",
    "rf5_suspicious_routing":      "RF-5: 輸送経路・経由地が不自然",
    "rf6_no_technical_knowledge":  "RF-6: 技術的知識が乏しい・質問を回避",
    "rf7_concern_country_routing": "RF-7: 懸念国・懸念地域経由の取引",
}

# EAR Country Chart 制御列ラベル（15 CFR Part 738 Supplement 1）
_CONTROL_REASON_LABELS: dict[str, str] = {
    "ns1": "NS1（National Security Col.1 — Wassenaar List 対象品目）",
    "ns2": "NS2（National Security Col.2 — EAR99 相当含む汎用品）",
    "mt":  "MT（Missile Technology — MTCR Annex 対象品目）",
    "np1": "NP1（Nuclear Nonproliferation Col.1 — NSG Trigger List）",
    "np2": "NP2（Nuclear Nonproliferation Col.2 — NSG Dual-Use）",
    "cb1": "CB1（Chem & Bio Col.1 — Australia Group Schedule 1/2）",
    "cb2": "CB2（Chem & Bio Col.2 — Australia Group Schedule 3）",
    "cb3": "CB3（Chem & Bio Col.3 — 化学・生物関連汎用品）",
    "rs1": "RS1（Regional Stability Col.1 — 兵器・軍用品）",
    "rs2": "RS2（Regional Stability Col.2 — 汎用品）",
    "at1": "AT1（Anti-Terrorism Col.1 — E:1 テロ支援国家指定）",
    "at2": "AT2（Anti-Terrorism Col.2 — 軍関連品 非E:1）",
    "fc1": "FC1（Firearms Convention — OAS 銃器条約締約国）",
}


# ──────────────────────────────────────────────
# 懸念国データのロード
# ──────────────────────────────────────────────

@lru_cache(maxsize=1)
def _load_concern_countries() -> dict[str, dict[str, Any]]:
    """
    catchall_concern_countries.json を読み込み、ISO alpha-2 をキーとする辞書を返す。
    lru_cache で初回のみファイル読み込みを行う。
    """
    try:
        raw = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
        return {entry["iso_code"]: entry for entry in raw}
    except Exception as e:
        logger.warning("キャッチオール懸念国データの読み込みに失敗しました: %s", e)
        return {}


def get_country_info(iso_code: str) -> dict[str, Any] | None:
    """ISO alpha-2 から国情報を取得する（懸念国・ホワイト国両方）。未登録の場合は None を返す。"""
    if not iso_code:
        return None
    return _load_concern_countries().get(iso_code.upper())


# ──────────────────────────────────────────────
# メイン評価関数
# ──────────────────────────────────────────────

def evaluate_catchall(ctx: CatchallContext) -> CatchallJudgment:
    """
    キャッチオール自己判定を実行し CatchallJudgment を返す。

    Args:
        ctx: CatchallContext — 仕向地・エンドユーザー・Red Flag 等の入力情報

    Returns:
        CatchallJudgment — 総合判定結果（verdict + 根拠リスト + 推奨アクション）
    """
    reasons: list[CatchallJudgmentReason] = []
    applicable_types: list[CatchallType] = []

    # ── Step 1: インフォーム通知チェック ──────────────────────────────────────
    if ctx.inform_received is True:
        reasons.append(CatchallJudgmentReason(
            code="INFORM_RECEIVED",
            label="インフォーム通知受領",
            detail=(
                "経済産業省から当該取引に係るインフォーム通知を受領しています。"
                "輸出令第4条に基づき、輸出許可申請が法的に義務付けられます。"
            ),
        ))
        applicable_types.append(CatchallType.WMD)
        return _build_judgment(
            ctx=ctx,
            verdict=CatchallVerdict.REQUIRES_PERMIT,
            verdict_label="輸出許可申請 必須",
            verdict_detail=(
                "インフォーム通知を受領しているため、輸出令第4条の規定により"
                "本取引の輸出許可申請が義務付けられます。"
            ),
            reasons=reasons,
            applicable_types=applicable_types,
            recommended_actions=[
                "速やかに安全保障貿易管理部門へ報告",
                "経済産業省への輸出許可申請を準備",
                "取引を一時中断し、許可取得前の出荷を行わない",
                "インフォーム通知の内容を取引記録として保管",
            ],
        )

    # ── Step 2: 仕向地情報取得 ────────────────────────────────────────────────
    dest = ctx.destination_country or ctx.end_user_country
    country_info = get_country_info(dest) if dest else None

    # EAR / 外為法ステータスの抽出
    fefta_status_raw: str | None = country_info.get("fefta_status") if country_info else None
    ear_groups: list[str] = country_info.get("ear_groups", []) if country_info else []
    ear_is_embargoed: bool = country_info.get("ear_is_embargoed", False) if country_info else False
    control_reasons_raw: dict[str, bool] | None = country_info.get("control_reasons") if country_info else None
    whitelist_agreements: list[str] = country_info.get("whitelist_agreements", []) if country_info else []

    is_whitelist = (fefta_status_raw == FeftaStatus.WHITELIST.value)
    is_concern = (
        country_info is not None
        and not is_whitelist
        and country_info.get("fefta_status") == FeftaStatus.CONCERN.value
    )
    risk_level: CountryRiskLevel | None = None
    dest_name: str | None = None

    if country_info:
        dest_name = country_info.get("name_ja")
        raw_level = country_info.get("level")

    # ── Step 1.5: E:1 禁輸国（テロ支援国家）チェック ─────────────────────────
    if ear_is_embargoed:
        reasons.append(CatchallJudgmentReason(
            code="EAR_E1_EMBARGOED",
            label=f"E:1 禁輸国: {dest_name or dest}",
            detail=(
                f"仕向地「{dest_name or dest}」は米国 BIS EAR の E:1（テロ支援国家）"
                "に指定されており、外為法上の最高リスク国です。"
                "輸出令第4条に基づく許可申請が必須です。"
            ),
        ))
        applicable_types.extend([CatchallType.WMD, CatchallType.CONVENTIONAL])
        return _build_judgment(
            ctx=ctx,
            verdict=CatchallVerdict.REQUIRES_PERMIT,
            verdict_label="輸出許可申請 必須（E:1 禁輸国）",
            verdict_detail=(
                f"仕向地「{dest_name or dest}」は米国 EAR の E:1（テロ支援国家）に該当します。"
                "輸出令第4条・第4条の2の双方が適用され、輸出許可申請が義務付けられます。"
            ),
            reasons=reasons,
            applicable_types=applicable_types,
            fefta_status=fefta_status_raw,
            ear_groups=ear_groups,
            ear_is_embargoed=ear_is_embargoed,
            control_reasons=control_reasons_raw,
            whitelist_agreements=whitelist_agreements,
            recommended_actions=[
                "即刻取引を停止し、安全保障貿易管理部門へ報告",
                "経済産業省への輸出許可申請（E:1 国向け）を準備",
                "法務・コンプライアンス部門と協議",
                "米国再輸出規制（EAR）の確認も併せて実施",
            ],
        )

    # ── Step 2: 仕向地リスクチェック ──────────────────────────────────────────
    if country_info:
        raw_level = country_info.get("level")

        if is_whitelist:
            # ホワイト国: 通常兵器キャッチオール除外、WMDは全仕向地対象
            risk_level = CountryRiskLevel.WHITELIST
            applicable_types.append(CatchallType.WMD)
            reasons.append(CatchallJudgmentReason(
                code="WHITELIST_COUNTRY",
                label=f"ホワイト国: {dest_name}（輸出令別表第3の2）",
                detail=(
                    f"仕向地「{dest_name}（{dest}）」は輸出令別表第3の2掲載のホワイト国です。"
                    "通常兵器キャッチオール（第4条の2）の適用除外ですが、"
                    "大量破壊兵器等キャッチオール（第4条）は全仕向地が対象です。"
                    + (f" 参加多国間体制: {', '.join(whitelist_agreements)}" if whitelist_agreements else "")
                ),
            ))
        else:
            # 懸念国
            risk_level = CountryRiskLevel(raw_level) if raw_level in ("HIGH", "MEDIUM", "LOW") else CountryRiskLevel.MEDIUM
            if country_info.get("wmd_catchall"):
                applicable_types.append(CatchallType.WMD)
            if country_info.get("conventional_catchall"):
                applicable_types.append(CatchallType.CONVENTIONAL)

            ear_group_str = ", ".join(ear_groups) if ear_groups else "—"
            reasons.append(CatchallJudgmentReason(
                code=f"CONCERN_COUNTRY_{raw_level}",
                label=f"懸念国該当: {dest_name}（リスク: {raw_level}）",
                detail=(
                    f"仕向地または最終需要者所在国「{dest_name}（{dest}）」は"
                    f"輸出令別表第3掲載の懸念国（リスクレベル: {raw_level}）に該当します。"
                    f" EAR Country Groups: {ear_group_str}。"
                    f" {country_info.get('note', '')}"
                ),
            ))

            # EAR Country Chart 活性列をエビデンスとして追加
            if control_reasons_raw:
                active_cols = [k for k, v in control_reasons_raw.items() if v]
                if active_cols:
                    col_labels = [_CONTROL_REASON_LABELS.get(c, c) for c in active_cols]
                    reasons.append(CatchallJudgmentReason(
                        code="EAR_COUNTRY_CHART",
                        label=f"EAR Country Chart 制御列: {', '.join(active_cols).upper()}",
                        detail=(
                            "米国 BIS EAR Country Chart（15 CFR Part 738 Supplement 1）において"
                            f"以下の制御列が該当します: {'; '.join(col_labels)}。"
                            "これらは本品目の輸出管理強度を示す参考情報です。"
                        ),
                    ))
    elif dest:
        # データベース未登録の仕向地 → WMD のみ適用
        applicable_types.append(CatchallType.WMD)
        reasons.append(CatchallJudgmentReason(
            code="DESTINATION_NON_CONCERN",
            label=f"仕向地（{dest}）: 懸念国非該当",
            detail=(
                f"仕向地「{dest}」は輸出令別表第3の懸念国リストに該当しません。"
                "通常兵器キャッチオール（第4条の2）の適用除外ですが、"
                "大量破壊兵器等キャッチオール（第4条）は全仕向地が対象です。"
            ),
        ))
    else:
        applicable_types.append(CatchallType.WMD)
        reasons.append(CatchallJudgmentReason(
            code="DESTINATION_UNKNOWN",
            label="仕向地未確認",
            detail="仕向地が特定されていません。エンドユーザーおよび最終仕向地の確認が必要です。",
        ))

    # ── Step 3: Red Flag チェック ─────────────────────────────────────────────
    positive_flags = ctx.red_flags.positive_flags()
    for flag_key in positive_flags:
        label = _RED_FLAG_LABELS.get(flag_key, flag_key)
        reasons.append(CatchallJudgmentReason(
            code=f"RED_FLAG_{flag_key.upper()}",
            label=f"Red Flag 該当: {label}",
            detail=(
                f"「{label}」が確認されました。"
                "METI 安全保障貿易管理ガイダンスに基づき、この取引は精査が必要です。"
            ),
        ))

    # ── Step 4: EUC 未取得チェック ────────────────────────────────────────────
    if ctx.euc_obtained is False and is_concern:
        reasons.append(CatchallJudgmentReason(
            code="EUC_NOT_OBTAINED",
            label="最終需要者証明書（EUC）未取得",
            detail=(
                "懸念国向け取引において最終需要者証明書（EUC）が取得されていません。"
                "エンドユーザーの身元・用途の確認が不十分です。"
            ),
        ))

    # ── Step 5: 総合判定ロジック ──────────────────────────────────────────────
    rf_count = len(positive_flags)
    answered_count = ctx.red_flags.answered_count()

    if answered_count == 0:
        return _build_judgment(
            ctx=ctx,
            verdict=CatchallVerdict.INDETERMINATE,
            verdict_label="判定不能（Red Flag 未回答）",
            verdict_detail=(
                "Red Flag チェックリストが未回答のため、キャッチオール規制の"
                "該当性を判定できません。7項目全ての回答が必要です。"
            ),
            reasons=reasons,
            applicable_types=applicable_types,
            is_concern=is_concern,
            is_whitelist=is_whitelist,
            risk_level=risk_level,
            dest_name=dest_name,
            positive_flags=positive_flags,
            fefta_status=fefta_status_raw,
            ear_groups=ear_groups,
            ear_is_embargoed=ear_is_embargoed,
            control_reasons=control_reasons_raw,
            whitelist_agreements=whitelist_agreements,
            recommended_actions=[
                "Red Flag チェックリスト（7項目）を全て回答してください",
                "エンドユーザーおよび最終用途の詳細を確認してください",
            ],
        )

    if rf_count >= 2 or (rf_count >= 1 and risk_level == CountryRiskLevel.HIGH):
        verdict = CatchallVerdict.REQUIRES_PERMIT
        verdict_label = "輸出許可申請 要確認"
        verdict_detail = (
            f"Red Flag が {rf_count} 件検出されました"
            + (f"（懸念国: {dest_name}）" if dest_name and is_concern else "")
            + "。輸出令第4条に基づき、輸出許可申請の要否を確認してください。"
        )
        actions = [
            "安全保障貿易管理部門へエスカレーション",
            "Red Flag 該当事項について取引先に詳細確認を要求",
            "最終用途・最終需要者の証明書類を取得",
            "経済産業省への事前相談を検討",
            "本判定記録を輸出管理ファイルに保管",
        ]
    elif rf_count == 1 or is_concern:
        verdict = CatchallVerdict.CAUTION
        verdict_label = "要注意（モニタリング継続）"
        verdict_detail = (
            ("Red Flag が 1 件検出されました。" if rf_count == 1 else "")
            + (f"仕向地「{dest_name}」は懸念国に該当します。" if is_concern and dest_name else "")
            + "引き続き取引内容を精査し、状況変化に注意してください。"
        )
        actions = [
            "取引先・エンドユーザーの詳細調査を実施",
            "最終用途申告書・最終需要者証明書を取得",
            "取引経緯・支払い方法を記録・保管",
            "懸念事項が増加した場合は安全保障貿易管理部門へ報告",
        ]
    elif is_whitelist:
        verdict = CatchallVerdict.CLEAR
        verdict_label = "通常兵器キャッチオール除外（ホワイト国）"
        verdict_detail = (
            f"仕向地「{dest_name}」は輸出令別表第3の2掲載のホワイト国であり、"
            "通常兵器キャッチオール（第4条の2）の適用除外です。"
            "Red Flag も検出されず、現時点でキャッチオール規制の該当性は低いと判断されます。"
        )
        actions = [
            "本判定結果を輸出管理記録として保管",
            "大量破壊兵器等キャッチオール（第4条）は引き続き適用されることに留意",
            "取引状況（用途変更・転売等）が生じた場合は再判定を実施",
        ]
    else:
        verdict = CatchallVerdict.CLEAR
        verdict_label = "キャッチオール非該当（現時点）"
        verdict_detail = (
            "Red Flag は検出されず、仕向地も懸念国に該当しません。"
            "現時点でキャッチオール規制の該当性は低いと判断されます。"
            "ただし、取引状況が変化した場合は再判定を実施してください。"
        )
        actions = [
            "本判定結果を輸出管理記録として保管",
            "取引状況（用途変更・転売等）が生じた場合は再判定を実施",
        ]

    return _build_judgment(
        ctx=ctx,
        verdict=verdict,
        verdict_label=verdict_label,
        verdict_detail=verdict_detail,
        reasons=reasons,
        applicable_types=applicable_types,
        is_concern=is_concern,
        is_whitelist=is_whitelist,
        risk_level=risk_level,
        dest_name=dest_name,
        positive_flags=positive_flags,
        fefta_status=fefta_status_raw,
        ear_groups=ear_groups,
        ear_is_embargoed=ear_is_embargoed,
        control_reasons=control_reasons_raw,
        whitelist_agreements=whitelist_agreements,
        recommended_actions=actions,
    )


# ──────────────────────────────────────────────
# 内部ヘルパー
# ──────────────────────────────────────────────

def _build_judgment(
    ctx: CatchallContext,
    verdict: CatchallVerdict,
    verdict_label: str,
    verdict_detail: str,
    reasons: list[CatchallJudgmentReason],
    applicable_types: list[CatchallType],
    recommended_actions: list[str],
    is_concern: bool = False,
    is_whitelist: bool = False,
    risk_level: CountryRiskLevel | None = None,
    dest_name: str | None = None,
    positive_flags: list[str] | None = None,
    fefta_status: str | None = None,
    ear_groups: list[str] | None = None,
    ear_is_embargoed: bool = False,
    control_reasons: dict[str, bool] | None = None,
    whitelist_agreements: list[str] | None = None,
) -> CatchallJudgment:
    dest = ctx.destination_country or ctx.end_user_country
    return CatchallJudgment(
        transaction_id=ctx.transaction_id,
        session_id=ctx.session_id,
        evaluated_at=ctx.evaluated_at,
        applicable_types=list(dict.fromkeys(applicable_types)),  # 重複除去・順序保持
        destination_country=dest,
        destination_risk_level=risk_level,
        destination_country_name=dest_name,
        is_concern_country=is_concern,
        is_whitelist_country=is_whitelist,
        fefta_status=fefta_status,
        ear_groups=ear_groups or [],
        ear_is_embargoed=ear_is_embargoed,
        control_reasons=control_reasons,
        whitelist_agreements=whitelist_agreements or [],
        red_flag_positive_count=len(positive_flags) if positive_flags else 0,
        red_flag_keys=positive_flags or [],
        verdict=verdict,
        verdict_label=verdict_label,
        verdict_detail=verdict_detail,
        reasons=reasons,
        recommended_actions=recommended_actions,
    )
