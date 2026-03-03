"""control_nodes.json ルックアップサービス。

data/unified/control_nodes.json を起動時に1回だけ読み込み、
item_no（例: "EL-7-19"）をキーにした逆引きインデックスを保持する。

two_list.py の compute_two_lists() から呼ばれ、
外為法条文テキスト・ECCN参照・WA参照などを判定結果に付与する。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional


def _project_root() -> str:
    here = os.path.abspath(os.path.dirname(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", "..", ".."))


_CONTROL_NODES_PATH = os.path.join(
    _project_root(), "data", "unified", "control_nodes.json"
)


class ControlNodeLookup:
    """シングルトン: control_nodes.json の逆引きインデックス。"""

    _instance: Optional["ControlNodeLookup"] = None

    @classmethod
    def get(cls) -> "ControlNodeLookup":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """テスト / ビルド後の再読み込み用。"""
        cls._instance = None

    def __init__(self) -> None:
        # item_no → regulation node
        self._reg_index: dict[str, dict[str, Any]] = {}
        # eccn code → eccn node
        self._eccn_index: dict[str, dict[str, Any]] = {}
        # wassenaar wa_id → wa node
        self._wa_index: dict[str, dict[str, Any]] = {}
        self._loaded = False
        self._load()

    def _load(self) -> None:
        if not os.path.exists(_CONTROL_NODES_PATH):
            return
        try:
            with open(_CONTROL_NODES_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        for node in data.get("nodes", []):
            ntype = node.get("type", "")
            if ntype == "regulation":
                item_no = node.get("item_no", "")
                if item_no:
                    self._reg_index[item_no] = node
            elif ntype == "eccn":
                eccn_id = node.get("item_no") or node.get("id", "")
                if eccn_id:
                    self._eccn_index[eccn_id] = node
            elif ntype == "wassenaar":
                wa_id = node.get("item_no") or node.get("id", "")
                if wa_id:
                    self._wa_index[wa_id] = node

        self._loaded = True

    @property
    def loaded(self) -> bool:
        return self._loaded

    # ──────────────────────────────────────────────────────────────
    def enrich_item(self, item_ids: list[str]) -> dict[str, Any]:
        """item_ids リスト（["EL-7-19"] など）から enrichment dict を返す。

        Returns dict with keys:
          fefta_category_label  : str  外為法 七の項（集積回路等）
          fefta_category_text   : str  別表第一 公式条文テキスト (max 400字)
          fefta_category_summary: str  主要品目の要約
          eccn_refs             : list[str]  対応 ECCN コード
          eccn_descriptions     : list[str]  ECCN の説明文
          bis_controls          : list[str]  BIS 制御理由 (NS, MT, NP ...)
          wa_refs               : list[str]  対応 WA エントリー
          has_enrichment        : bool
        """
        result: dict[str, Any] = {
            "fefta_category_label":   "",
            "fefta_category_text":    "",
            "fefta_category_summary": "",
            "eccn_refs":              [],
            "eccn_descriptions":      [],
            "bis_controls":           [],
            "wa_refs":                [],
            "has_enrichment":         False,
        }
        if not item_ids or not self._loaded:
            return result

        # item_ids の最初のヒットを採用（複数あれば結合）
        for item_id in item_ids:
            node = self._reg_index.get(item_id)
            # 完全一致なし、またはラベルなし → カテゴリプレフィックスでフォールバック
            # 例: "EL-7" ノードが存在しても fefta_category_label が空なら "EL-7-xx" を参照
            if not node or not node.get("fefta_category_label"):
                prefix = item_id + "-"
                fallback = next(
                    (v for k, v in self._reg_index.items() if k.startswith(prefix)),
                    None,
                )
                if fallback:
                    node = fallback
            if not node:
                continue

            result["has_enrichment"] = True

            if not result["fefta_category_label"]:
                result["fefta_category_label"] = node.get("fefta_category_label", "")
            if not result["fefta_category_text"]:
                result["fefta_category_text"] = (node.get("fefta_category_text") or "")[:400]
            if not result["fefta_category_summary"]:
                result["fefta_category_summary"] = (node.get("fefta_category_summary") or "")[:200]

            # ECCN explicit edges
            for edge in node.get("edges", {}).get("explicit", []):
                eccn_code = edge.get("target_id", "")
                if eccn_code and eccn_code not in result["eccn_refs"]:
                    result["eccn_refs"].append(eccn_code)
                    # ECCN ノードから説明を取得
                    eccn_node = self._eccn_index.get(eccn_code, {})
                    if eccn_node.get("description"):
                        result["eccn_descriptions"].append(eccn_node["description"][:80])
                    if eccn_node.get("bis_controls"):
                        for c in eccn_node["bis_controls"]:
                            if c not in result["bis_controls"]:
                                result["bis_controls"].append(c)

        return result


# ──────────────────────────────────────────────────────────────────────
# モジュールレベルの便利関数
# ──────────────────────────────────────────────────────────────────────

def enrich_two_list_item(item: dict[str, Any]) -> dict[str, Any]:
    """compute_two_lists() の結果 1件に enrichment を付与して返す（in-place + return）。"""
    item_ids: list[str] = item.get("item_ids") or []
    enrichment = ControlNodeLookup.get().enrich_item(item_ids)
    item.update(enrichment)
    return item
