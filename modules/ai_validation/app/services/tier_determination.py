"""
審査承認ティア自動判定サービス。

AI解析（2リスト照合）完了後に呼ばれ、以下のティアを返す:
  Tier 1 (auto_clear):      外為法非該当・EAR99・スクリーニングクリア → 自動承認
  Tier 2 (standard_review): キャッチオール自己判定が必要
  Tier 3 (license_required): 外為法該当 or 2リスト交差 or スクリーニングHIT → 輸出許可要否確認が必要
"""

from __future__ import annotations

_CONCERN_COUNTRIES = frozenset({"CN", "RU", "IR", "KP", "SY", "CU"})
_EAR99_VALUES      = frozenset({"EAR99", "EAR 99", "N/A", ""})

TIER_LABELS = {
    1: "自動承認（Tier 1）",
    2: "標準審査（Tier 2）",
    3: "輸出許可確認（Tier 3）",
}

TIER_STEP_MAP = {
    1: ["screening", "ai_run"],
    2: ["screening", "ai_run", "catchall"],
    3: ["screening", "ai_run", "catchall"],
}


def determine_tier(
    two_list_result: dict | None,
    eccn: str | None,
    screening_status: str | None,
    destination_country: str | None,
) -> tuple[int, list[str], str]:
    """
    Returns (tier, required_steps, reason).

    Args:
        two_list_result: compute_two_lists() の戻り値
        eccn:            品目ECCN（品目管理から参照、なければ None）
        screening_status: tx.screening_status（"no_match" / "hit" / None）
        destination_country: ISO alpha-2
    """
    counts = (two_list_result or {}).get("counts", {})
    intersection = int(counts.get("intersection", 0) or 0)
    core_only    = int(counts.get("core_only", 0) or 0)

    # スクリーニングヒット
    screen_hit = bool(
        screening_status
        and screening_status.strip().lower() not in ("no_match", "clear", "")
    )

    # EAR99 判定
    eccn_upper = (eccn or "").strip().upper()
    is_ear99   = eccn_upper in _EAR99_VALUES

    # 懸念国判定
    is_concern = bool(
        destination_country
        and destination_country.strip().upper() in _CONCERN_COUNTRIES
    )

    # ── Tier 3: 輸出許可確認が必要 ──────────────────────────────────
    if screen_hit:
        return 3, TIER_STEP_MAP[3], "スクリーニングにヒットあり → 輸出許可要否の確認が必要です"

    if intersection > 0:
        return 3, TIER_STEP_MAP[3], (
            f"外為法×EAR 交差ヒット {intersection}件 → 輸出許可申請が必要な可能性があります"
        )

    if core_only > 0:
        return 3, TIER_STEP_MAP[3], (
            f"外為法直接ヒット {core_only}件 → 輸出許可申請が必要な可能性があります"
        )

    # ── Tier 2: キャッチオール判定が必要 ────────────────────────────
    if not is_ear99:
        return 2, TIER_STEP_MAP[2], (
            f"ECCN {eccn} → EAR 規制品のためキャッチオール自己判定が必要です"
        )

    if is_concern:
        return 2, TIER_STEP_MAP[2], (
            f"仕向国 {destination_country}（Country Group E:1）→ キャッチオール自己判定が必要です"
        )

    # ── Tier 1: 自動承認 ─────────────────────────────────────────────
    return 1, TIER_STEP_MAP[1], "外為法非該当・EAR99相当・スクリーニングクリア → 自動承認"
