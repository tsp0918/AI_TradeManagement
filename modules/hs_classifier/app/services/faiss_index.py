"""
faiss_index.py  (Layer C アダプタ)
─────────────────────────────────────────────────────────────────────────────
旧来の 2 段階ローカル FAISS を廃止し、platform_core.services.faiss_e5_service の
Layer C インデックス（e5-large, dim=1024）に委譲するアダプタ。

公開インターフェース（routers / services が依存）:
  is_built() -> bool
  ntotal() -> int
  search(query, top_k, ...) -> list[dict]   # HSCandidate 互換 dict
  lookup(hs_code) -> dict | None
"""
from __future__ import annotations

import logging

from platform_core.services.faiss_e5_service import (
    LayerCHit,
    is_ready,
    layer_c_available,
    lookup_layer_c,
    ntotal_layer_c,
    search_layer_c,
)

logger = logging.getLogger(__name__)


def is_built() -> bool:
    """インデックスが使用可能かどうかを返す。"""
    return is_ready() and layer_c_available()


def ntotal() -> int:
    return ntotal_layer_c()


def search(
    query: str,
    top_k: int = 5,
    fefta_filter: list[str] | None = None,
    # 旧 stage1_top_chapters パラメータは無視（Layer C では不要）
    stage1_top_chapters: int | None = None,
) -> list[dict]:
    """Layer C で HS コードを検索し、HSCandidate 互換の dict リストを返す。

    返却 dict のキー:
      hs_code, description_en, section, chapter, chapter_description,
      heading, heading_description, atlas_short_name, similarity_score,
      hs2017_codes, hs_version, fefta_items, has_fefta_mapping
    """
    hits: list[LayerCHit] = search_layer_c(query, top_k=top_k, fefta_filter=fefta_filter)
    return [_hit_to_dict(h) for h in hits]


def lookup(hs_code: str) -> dict | None:
    """hs_code からメタデータ dict を返す。見つからなければ None。"""
    return lookup_layer_c(hs_code)


def _hit_to_dict(h: LayerCHit) -> dict:
    """LayerCHit → HSCandidate 互換 dict へ変換。"""
    return {
        "hs_code":             h.hs_code,
        "description_en":      h.description,       # Layer C は description (英語)
        "section":             "",                   # Layer C メタに section なし
        "chapter":             h.chapter,
        "chapter_description": None,
        "heading":             h.heading,
        "heading_description": None,
        "atlas_short_name":    None,
        "similarity_score":    h.score,
        "hs2017_codes":        [],
        # Layer C 追加フィールド
        "hs_version":          h.hs_version,
        "fefta_items":         h.fefta_items,
        "has_fefta_mapping":   h.has_fefta_mapping,
        "subheading":          h.subheading,
        "level":               h.level,
    }
