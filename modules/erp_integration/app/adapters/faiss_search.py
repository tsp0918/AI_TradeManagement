"""platform-core FAISS Layer A (port 8000) への呼び出しアダプター。"""
from __future__ import annotations

import httpx

from ..config import settings

# 外為法/ECCN 判定に使うスコア閾値
_APPLICABLE_THRESHOLD = 0.82
_PENDING_THRESHOLD    = 0.75

# 高リスク ECCN プレフィックス（ITAR/MTCR/NBG 管理品目）
_HIGH_RISK_ECCN_PREFIXES = (
    "0A", "0B", "0C", "0D", "0E",   # Weapons
    "1C3", "1C4", "1C5",             # CBW precursors
    "3A001", "3A002",                # Semiconductors
    "6A002", "6A004",                # Sensors/lasers
    "9A004", "9A005", "9A515",       # Rockets/space
)


async def search_layer_a(query: str, top_k: int = 5) -> list[dict]:
    """
    GET /api/faiss/search/layer-a?q=...
    Returns list of LayerAResult dicts.
    """
    async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
        resp = await client.get(
            f"{settings.PLATFORM_CORE_URL}/api/faiss/search/layer-a",
            params={"q": query, "top_k": top_k},
        )
        resp.raise_for_status()
        data = resp.json()
    return data.get("hits", [])


def derive_judgment(hits: list[dict], hs_code: str | None = None) -> dict:
    """
    FAISS ヒットから FEFTA 判定・ECCN を推定する。

    Returns:
      judgment: APPLICABLE / NOT_APPLICABLE / PENDING
      eccn: str | None
      item_number: str | None
      requires_license: bool
      rationale: str
    """
    if not hits:
        return {
            "judgment": "NOT_APPLICABLE",
            "eccn": None,
            "item_number": None,
            "requires_license": False,
            "rationale": "No relevant regulatory entries found in Layer A index.",
        }

    top = hits[0]
    score = float(top.get("score", 0.0))
    source_type = top.get("source_type", "")
    item_no = top.get("item_no", "") or ""
    eccn_raw = top.get("item_no", "") if source_type == "eccn" else None

    # ECCN ヒットから推定
    eccn = None
    if source_type == "eccn":
        eccn = item_no if item_no else None
    else:
        # 外為法ヒット → EAR99 相当の可能性あり。上位に eccn ヒットがあれば採用
        for h in hits[1:]:
            if h.get("source_type") == "eccn":
                eccn = h.get("item_no") or None
                break

    # スコアで判定
    if score >= _APPLICABLE_THRESHOLD:
        judgment = "APPLICABLE"
        requires_license = True
    elif score >= _PENDING_THRESHOLD:
        judgment = "PENDING"
        requires_license = False
    else:
        judgment = "NOT_APPLICABLE"
        requires_license = False

    # USML/Wassenaar ヒットは強制 APPLICABLE
    if source_type in ("usml_itar", "wassenaar_ml") and score >= 0.70:
        judgment = "APPLICABLE"
        requires_license = True

    # 外為法 item_no
    fefta_item = item_no if source_type == "law" else None

    rationale_parts = [f"Top match score: {score:.3f}"]
    if eccn:
        rationale_parts.append(f"ECCN: {eccn}")
    if fefta_item:
        rationale_parts.append(f"外為法 {fefta_item}項")

    return {
        "judgment": judgment,
        "eccn": eccn,
        "item_number": fefta_item,
        "requires_license": requires_license,
        "rationale": " | ".join(rationale_parts),
    }
