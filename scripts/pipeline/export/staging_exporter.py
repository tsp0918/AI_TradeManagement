"""
export/staging_exporter.py
data/staging/ の成果物を本番パス（data/artifacts/ または modules/*/data/faiss/）へ
プロモーション（コピー）するユーティリティ。

ルール:
  - staging への書き込みは常に許可
  - 本番反映は promote() の明示的な呼び出しのみ
  - 既存本番ファイルはバックアップ（.bak タイムスタンプ）してから上書き
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[3]

# staging → 本番 のマッピング
PROMOTION_TARGETS = {
    # 制裁FAISSインデックス
    "faiss/entities.index":      BASE_DIR / "modules" / "data" / "faiss" / "entities.index",
    "faiss/entities_meta.json":  BASE_DIR / "modules" / "data" / "faiss" / "entities_meta.json",
    # 規制マトリクスFAISS
    "faiss/matrix_rules.index":  BASE_DIR / "modules" / "ai_validation" / "data" / "faiss" / "matrix_rules.index",
    "faiss/matrix_rules_meta.json": BASE_DIR / "modules" / "ai_validation" / "data" / "faiss" / "matrix_rules_meta.json",
    # マッピングテーブル
    "mappings/ipc_eccn_mapping.json":         BASE_DIR / "data" / "source" / "eccn" / "ipc_eccn_mapping.json",
    "mappings/hs_fefta_mapping.json":         BASE_DIR / "data" / "source" / "hs" / "hs_fefta_mapping.json",
    "mappings/regulatory_keyword_dict.json":  BASE_DIR / "data" / "source" / "eccn" / "regulatory_keyword_dict.json",
    # FEFTA サプリメント
    "fefta/fefta_supplement.json": BASE_DIR / "data" / "source" / "fefta" / "fefta_supplement.json",
}


def promote(
    staging_dir: Path,
    targets: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, str]:
    """
    staging 成果物を本番パスにコピー（バックアップ付き）。

    Parameters
    ----------
    staging_dir : data/staging/ のルート
    targets     : コピー対象キーのリスト（None = 全て）
    dry_run     : True の場合コピーを実行しない

    Returns
    -------
    dict: { target_key: "promoted" | "skipped" | "error: ..." }
    """
    staging_dir = Path(staging_dir)
    results: dict[str, str] = {}
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    for key, dest_path in PROMOTION_TARGETS.items():
        if targets and key not in targets:
            results[key] = "skipped (not in targets)"
            continue

        src_path = staging_dir / key
        if not src_path.exists():
            results[key] = "skipped (source not found)"
            logger.warning("Staging file not found: %s", src_path)
            continue

        if dry_run:
            results[key] = f"[DRY_RUN] would copy {src_path} → {dest_path}"
            logger.info(results[key])
            continue

        try:
            # バックアップ
            if dest_path.exists():
                bak = dest_path.with_suffix(f".{ts}.bak")
                shutil.copy2(dest_path, bak)
                logger.info("Backup: %s → %s", dest_path.name, bak.name)

            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dest_path)
            logger.info("Promoted: %s → %s", src_path, dest_path)
            results[key] = "promoted"
        except Exception as exc:
            results[key] = f"error: {exc}"
            logger.error("Failed to promote %s: %s", key, exc)

    return results


def staging_summary(staging_dir: Path) -> dict[str, Any]:
    """staging ディレクトリの現在の成果物一覧を返す。"""
    staging_dir = Path(staging_dir)
    summary: dict[str, Any] = {}

    for path in sorted(staging_dir.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(staging_dir))
            size = path.stat().st_size
            info: dict[str, Any] = {"size_bytes": size}

            if path.suffix == ".json":
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        info["records"] = len(data)
                    elif isinstance(data, dict):
                        inner = data.get("nodes") or data
                        if isinstance(inner, (list, dict)):
                            info["records"] = len(inner)
                except Exception:
                    pass

            summary[rel] = info

    return summary
