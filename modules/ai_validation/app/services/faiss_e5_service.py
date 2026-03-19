"""
faiss_e5_service.py  (ai_validation re-export shim)
─────────────────────────────────────────────────────────────────────────────
実装は platform_core.services.faiss_e5_service に移動済み。
このファイルは後方互換のための再エクスポートのみ行う。
"""
from platform_core.services.faiss_e5_service import (  # noqa: F401
    LayerAHit,
    LayerBHit,
    LayerCHit,
    is_ready,
    layer_c_available,
    lookup_layer_c,
    ntotal_layer_a,
    ntotal_layer_b,
    ntotal_layer_c,
    preload,
    search_layer_a,
    search_layer_b,
    search_layer_c,
)
