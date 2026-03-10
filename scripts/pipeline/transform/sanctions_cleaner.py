"""
transform/sanctions_cleaner.py
制裁リストの統合・クレンジング・重複排除処理。

入力: OFAC SDN + BIS Entity List の SanctionEntity dicts
出力: クレンジング済みの統合リスト → data/staging/sanctions/sanctions_merged.json
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SanctionEntity = dict[str, Any]

_CORP_RE = re.compile(
    r"\b(?:co(?:mpany|rp(?:oration)?)?\.?|ltd\.?|inc\.?|llc\.?|gmbh\.?|"
    r"s\.?a\.?|b\.?v\.?|plc\.?|pte\.?\s*ltd\.?|"
    r"株式会社|有限会社|合同会社|合資会社)\b",
    re.IGNORECASE,
)


def clean_and_merge(
    entities: list[SanctionEntity],
    dry_run: bool = False,
    output_path: Path | None = None,
) -> list[SanctionEntity]:
    """
    複数ソースの SanctionEntity を統合・クレンジングして返す。

    処理:
      1. entity_name の正規化（法人格除去・大文字統一）
      2. uid + list_source ベースの重複排除
      3. 名前が空のレコードのスキップ
      4. aliases を list に統一

    Parameters
    ----------
    entities    : OFAC + BIS をまとめた入力リスト
    dry_run     : True の場合 output_path への書き込みをスキップ
    output_path : 保存先 (data/staging/sanctions/sanctions_merged.json)
    """
    logger.info("Input: %d entities before dedup", len(entities))

    seen_keys: set[str] = set()
    cleaned:   list[SanctionEntity] = []
    skipped = 0

    for e in entities:
        name = _normalize_name(e.get("entity_name", ""))
        if not name:
            skipped += 1
            continue

        # uid が空の場合は名前 + ソースのハッシュを代替IDにする
        uid = str(e.get("uid", "") or "")
        if not uid:
            uid = hashlib.md5(f"{e.get('list_source','')}/{name}".encode()).hexdigest()[:12]

        dedup_key = f"{e.get('list_source', '')}:{uid}"
        if dedup_key in seen_keys:
            skipped += 1
            continue
        seen_keys.add(dedup_key)

        # aliases を常に list に統一
        aliases = e.get("aliases", [])
        if isinstance(aliases, str):
            aliases = [aliases] if aliases else []

        cleaned.append(
            {
                "list_source":  e.get("list_source", ""),
                "entity_name":  name,
                "entity_name_normalized": name,
                "aliases":      [_normalize_name(a) for a in aliases if a],
                "address":      (e.get("address") or "").strip(),
                "country":      (e.get("country") or "").strip().upper(),
                "entity_type":  (e.get("entity_type") or "").strip(),
                "uid":          uid,
                # BIS 固有
                "license_requirement": e.get("license_requirement", ""),
                "license_policy":      e.get("license_policy", ""),
            }
        )

    logger.info(
        "After dedup: %d entities (%d skipped). Sources: %s",
        len(cleaned),
        skipped,
        _source_counts(cleaned),
    )

    if output_path and not dry_run:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("Saved to %s", output_path)
    elif dry_run:
        logger.info("[DRY_RUN] Output suppressed.")

    return cleaned


def _normalize_name(name: str) -> str:
    if not name:
        return ""
    # 法人格除去 → 連続空白圧縮 → strip
    normalized = _CORP_RE.sub(" ", name)
    return " ".join(normalized.split())


def _source_counts(entities: list[SanctionEntity]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in entities:
        src = e.get("list_source", "unknown")
        counts[src] = counts.get(src, 0) + 1
    return counts
