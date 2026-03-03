"""patents.json パーサー。

patents.json を読み込み、patent_index（要約リスト）を生成する。
regulation node への semantic link は builder.py で行う。
"""

from __future__ import annotations

import json
import pathlib
import re


# IPC コード → 規制ドメイン のヒントマッピング
_IPC_DOMAIN_HINTS: dict[str, list[str]] = {
    "G03F": ["フォトリソグラフィー", "レジスト", "露光"],
    "H01L": ["半導体", "集積回路", "デバイス"],
    "H01S": ["レーザ", "光源"],
    "C23C": ["成膜", "CVD", "スパッタ"],
    "B82Y": ["ナノテク", "ナノ材料"],
    "C08F": ["高分子", "ポリマー"],
    "C08G": ["高分子", "樹脂"],
    "F25B": ["冷却", "真空"],
    "H05K": ["プリント基板", "実装"],
    "B81B": ["MEMS", "微細加工"],
    "C07C": ["有機化合物", "化学物質"],
    "C07D": ["複素環化合物"],
    "G06F": ["半導体設計", "EDA"],
    "H04B": ["通信", "無線"],
    "H04L": ["暗号", "通信プロトコル"],
    "F41":  ["兵器"],
    "C06B": ["火薬", "爆発物"],
    "C06C": ["起爆装置"],
}


def _parse_ipc_codes(raw: str) -> list[str]:
    """IPC コード文字列（セミコロン区切り）をリストに変換。"""
    if not raw:
        return []
    codes = []
    for part in raw.split(";"):
        part = part.strip().split("/")[0]  # "G03F/00" → "G03F"
        if part:
            codes.append(part)
    return list(dict.fromkeys(codes))


def _ipc_domain_hints(ipc_codes: list[str]) -> list[str]:
    """IPC コードリストからドメインヒントを取得する。"""
    hints: list[str] = []
    for ipc in ipc_codes:
        for prefix, h in _IPC_DOMAIN_HINTS.items():
            if ipc.startswith(prefix):
                hints.extend(h)
    return list(dict.fromkeys(hints))


def parse_patents(patents_path: pathlib.Path) -> list[dict]:
    """patents.json を読み込み patent_index を返す。

    Returns:
        各要素の形式:
        {
          "id": "JP20194086277A",
          "type": "patent",
          "title": "...",
          "assignee": "...",
          "abstract_short": "...",  # 200文字
          "ipc_codes": ["G03F", "H01L"],
          "domain_hints": ["レジスト", "半導体"],
          "domain_tag": "フォトリソグラフィー/レジスト・現像・膜",
          "usecase_texts": ["..."],   # usecases[*].usecase_text
          "keywords": ["レジスト", "EUV", ...],   # 検索用
        }
    """
    with patents_path.open(encoding="utf-8") as f:
        patents: list[dict] = json.load(f)

    index: list[dict] = []

    for p in patents:
        pub_no    = p.get("publication_number", "")
        title     = p.get("title", "")
        assignee  = p.get("assignee", "")
        abstract  = p.get("abstract", "") or ""
        ipc_raw   = p.get("ipc_codes_raw", "") or ""
        domain_tag = p.get("_domain_tag", "") or ""

        ipc_codes    = _parse_ipc_codes(ipc_raw)
        domain_hints = _ipc_domain_hints(ipc_codes)

        usecases = p.get("usecases", []) or []
        usecase_texts = [
            u.get("usecase_text", "")
            for u in usecases
            if u.get("usecase_text")
        ][:3]  # 最大3件

        # キーワード抽出（タイトル + ドメインヒントから）
        keywords = list(dict.fromkeys(
            domain_hints + _extract_title_keywords(title)
        ))

        index.append({
            "id":            pub_no,
            "type":          "patent",
            "title":         title,
            "assignee":      assignee,
            "abstract_short": abstract[:200],
            "ipc_codes":     ipc_codes,
            "domain_hints":  domain_hints,
            "domain_tag":    domain_tag,
            "usecase_texts": usecase_texts,
            "keywords":      keywords,
        })

    return index


def _extract_title_keywords(title: str) -> list[str]:
    """タイトルから技術キーワードを抽出する（簡易）。"""
    keywords: list[str] = []
    patterns = [
        ("EUV",          ["EUV", "極端紫外", "13.5nm"]),
        ("ArF",          ["ArF", "193nm", "液浸"]),
        ("レジスト",     ["レジスト", "resist", "フォトレジスト"]),
        ("半導体",       ["半導体", "semiconductor"]),
        ("集積回路",     ["集積回路", "IC", "chip"]),
        ("レーザ",       ["レーザ", "laser"]),
        ("成膜",         ["成膜", "CVD", "ALD", "スパッタ"]),
        ("エッチング",   ["エッチング", "etch"]),
        ("露光",         ["露光", "lithography", "リソグラフィ"]),
        ("センサー",     ["センサー", "sensor", "検出"]),
        ("通信",         ["通信", "wireless", "5G", "RF"]),
        ("暗号",         ["暗号", "crypto", "encryption"]),
    ]
    title_lower = title.lower()
    for kw, triggers in patterns:
        if any(t.lower() in title_lower for t in triggers):
            keywords.append(kw)
    return keywords
