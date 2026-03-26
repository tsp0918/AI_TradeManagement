"""
日本ローカルHSコード（9桁 統計品目番号）サジェストAPI。

エンドポイント:
  GET /api/hs-local/jp/suggest?hs6=850440[&limit=20]
    → hs6（6桁）に対応する JP 9桁候補を返す。
    → hs6 を省略すると全件（最大 limit 件）。

  GET /api/hs-local/jp/search?q=電気変圧器[&limit=20]
    → description を部分一致で全文検索。

レスポンス:
  [
    {
      "hs6":         "850440",
      "jp_code":     "850440000",
      "description": "Transformers having a power handling ...",
      "tariff_rate": null,
      "tariff_type": null,
      "unit":        null,
      "source":      "seed_000pad"
    }, ...
  ]
"""
from __future__ import annotations

import json
import threading
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

_DATA_PATH = Path(__file__).resolve().parents[4] / "data" / "staging" / "jp_hs_local.json"

# ── インメモリキャッシュ ───────────────────────────────────────────────────────

_lock    = threading.Lock()
_records: list[dict] | None = None  # 全レコード
_by_hs6:  dict[str, list[dict]] | None = None  # hs6 → records


def _load() -> tuple[list[dict], dict[str, list[dict]]]:
    global _records, _by_hs6
    with _lock:
        if _records is not None:
            return _records, _by_hs6  # type: ignore[return-value]
        if not _DATA_PATH.exists():
            _records = []
            _by_hs6 = {}
            return _records, _by_hs6
        with open(_DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        _records = data.get("records", []) if isinstance(data, dict) else data
        _by_hs6 = {}
        for r in _records:
            _by_hs6.setdefault(r.get("hs6", ""), []).append(r)
        return _records, _by_hs6


# ── エンドポイント ─────────────────────────────────────────────────────────────

@router.get("/api/hs-local/jp/suggest")
def suggest_jp_hs(
    hs6:   Optional[str] = Query(None, description="6桁 HS コード（前方一致）"),
    limit: int           = Query(20,   ge=1, le=100),
):
    """6桁HSコードから日本9桁統計品目番号の候補を返す。"""
    records, by_hs6 = _load()

    if not records:
        raise HTTPException(
            status_code=503,
            detail="JP HS local data not available. Run scripts/build_jp_hs_local.py first.",
        )

    if hs6:
        # 完全一致 or 前方一致（6桁コードは普通完全一致）
        hs6_clean = hs6.strip().replace(".", "").replace("-", "")
        if len(hs6_clean) == 6:
            results = by_hs6.get(hs6_clean, [])
        else:
            # 前方一致 fallback
            results = [r for r in records if r.get("hs6", "").startswith(hs6_clean)]
    else:
        results = records

    return results[:limit]


@router.get("/api/hs-local/jp/search")
def search_jp_hs(
    q:     str = Query(..., description="英語または日本語の品名キーワード"),
    limit: int = Query(20, ge=1, le=100),
):
    """品名キーワードで JP 9桁候補を全文部分一致検索する。"""
    records, _ = _load()

    if not records:
        raise HTTPException(
            status_code=503,
            detail="JP HS local data not available. Run scripts/build_jp_hs_local.py first.",
        )

    q_lower = q.lower()
    results = [
        r for r in records
        if q_lower in r.get("description", "").lower()
    ]
    return results[:limit]


@router.get("/api/hs-local/jp/meta")
def jp_hs_meta():
    """データソースのメタ情報を返す。"""
    if not _DATA_PATH.exists():
        return {"available": False, "path": str(_DATA_PATH)}
    with open(_DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)
    meta = data.get("meta", {}) if isinstance(data, dict) else {}
    return {"available": True, **meta}
