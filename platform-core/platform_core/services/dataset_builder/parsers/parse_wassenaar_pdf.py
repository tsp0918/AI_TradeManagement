"""Wassenaar Arrangement 二重用途リスト PDF パーサー。

WA Public Documents Volume II の PDF から
Dual-Use Goods and Technologies リストのエントリーを抽出する。

エントリー形式例:
  1. A. 3. Manufactures of non-"fusible" aromatic polyimides...
  → category=1, control_type=A, item_no=3

出力フォーマット:
  {
    "wa_id":       "1.A.3",
    "category":    1,
    "control_type": "A",       # A=Equipment/Materials, B=Test, C=Materials, D=Software, E=Technology
    "item_no":     "3",
    "label":       "Manufactures of non-fusible...",
    "description": "...",
    "is_sensitive":      False,
    "is_very_sensitive": False,
    "source_file": "Wassenaar Arrangement.pdf",
  }

使用方法:
    from .parsers.parse_wassenaar_pdf import parse_wassenaar_pdf
    entries = parse_wassenaar_pdf(path)
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

try:
    import pdfplumber
    _BACKEND = "pdfplumber"
except ImportError:
    try:
        import PyPDF2  # noqa: F401
        _BACKEND = "pypdf2"
    except ImportError:
        _BACKEND = None

# ── WA エントリーの正規表現 ───────────────────────────────────────
# "1. A. 3. Description text" 形式
_WA_ENTRY_RE = re.compile(
    r"^(\d+)\.\s+([A-E])\.\s+(\d+[a-z]?)\.\s+(.*)",
    re.MULTILINE,
)

# カテゴリ見出し
_CATEGORY_HEADER_RE = re.compile(
    r"DUAL-USE LIST\s*-\s*CATEGORY\s+(\d+)\s*-\s*([A-Z][^\n_]+)",
    re.IGNORECASE,
)

# Sensitive / Very Sensitive リストのマーカー
_SENSITIVE_HEADER_RE = re.compile(
    r"(VERY SENSITIVE LIST|SENSITIVE LIST)",
    re.IGNORECASE,
)

# WA カテゴリ名
_WA_CATEGORIES: dict[int, str] = {
    0:  "核物質",
    1:  "特殊材料・関連設備",
    2:  "材料加工",
    3:  "エレクトロニクス",
    4:  "コンピュータ",
    5:  "通信・情報セキュリティ",
    6:  "センサー・レーザー",
    7:  "ナビゲーション・航空電子",
    8:  "海洋",
    9:  "航空宇宙・推進",
}

# 制御タイプ
_CONTROL_TYPES: dict[str, str] = {
    "A": "Equipment/Materials",
    "B": "Test & Inspection Equipment",
    "C": "Materials",
    "D": "Software",
    "E": "Technology",
}


def _extract_pages_text(pdf_path: pathlib.Path) -> list[str]:
    if _BACKEND == "pdfplumber":
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pdf:
            return [p.extract_text() or "" for p in pdf.pages]
    elif _BACKEND == "pypdf2":
        import PyPDF2
        pages: list[str] = []
        with pdf_path.open("rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                pages.append(page.extract_text() or "")
        return pages
    else:
        raise RuntimeError("PDF ライブラリが見つかりません")


def parse_wassenaar_pdf(
    pdf_path: pathlib.Path | str,
    max_pages: int | None = None,
) -> list[dict[str, Any]]:
    """Wassenaar Arrangement PDF から Dual-Use リストエントリーを抽出する。

    Returns:
        WA エントリーの list
    """
    pdf_path = pathlib.Path(pdf_path)
    source_file = pdf_path.name

    pages = _extract_pages_text(pdf_path)
    if max_pages:
        pages = pages[:max_pages]

    full_text = "\n".join(pages)
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Sensitive / Very Sensitive リストのページ範囲を特定
    sensitive_entries: set[str]      = set()
    very_sensitive_entries: set[str] = set()

    # Supplement 6/7 (Sensitive/Very Sensitive) のセクションを検索
    sens_section  = False
    vsens_section = False
    for line in full_text.split("\n"):
        if "VERY SENSITIVE LIST" in line.upper():
            vsens_section = True
            sens_section  = False
        elif "SENSITIVE LIST" in line.upper():
            sens_section  = True
            vsens_section = False
        # WA ID パターン (例: 3.E.1) を収集
        m = re.match(r"(\d+\.[A-E]\.\d+[a-z]?)", line.strip())
        if m:
            wa_id = m.group(1).replace(" ", "").replace(". ", ".")
            if vsens_section:
                very_sensitive_entries.add(wa_id)
            elif sens_section:
                sensitive_entries.add(wa_id)

    # メインリスト → エントリー抽出
    current_category: int = 0

    for m in _WA_ENTRY_RE.finditer(full_text):
        cat      = int(m.group(1))
        ctype    = m.group(2)
        item_no  = m.group(3)
        label    = m.group(2 + 2).strip()  # group(4)

        wa_id = f"{cat}.{ctype}.{item_no}"
        if wa_id in seen:
            continue
        seen.add(wa_id)

        # ブロック取得（次のエントリーまたは最大800文字）
        next_m = _WA_ENTRY_RE.search(full_text, m.end())
        end    = next_m.start() if next_m else m.start() + 800
        block  = full_text[m.start():min(end, m.start() + 800)]

        # 説明文（最初の段落）
        desc_lines: list[str] = []
        for line in block.split("\n")[1:4]:
            stripped = line.strip()
            if not stripped:
                break
            if re.match(r"^\d+\.\s+[A-E]\.\s+\d", stripped):
                break
            desc_lines.append(stripped)
        description = " ".join(desc_lines)[:300]

        entries.append({
            "wa_id":             wa_id,
            "category":          cat,
            "category_name":     _WA_CATEGORIES.get(cat, f"Category {cat}"),
            "control_type":      ctype,
            "control_type_name": _CONTROL_TYPES.get(ctype, ctype),
            "item_no":           item_no,
            "label":             label[:200],
            "description":       description,
            "is_sensitive":      wa_id in sensitive_entries,
            "is_very_sensitive": wa_id in very_sensitive_entries,
            "source_file":       source_file,
        })

    return entries


def build_wa_nodes(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """WA エントリーから control graph ノードを生成する。"""
    nodes: list[dict[str, Any]] = []
    for e in entries:
        nodes.append({
            "id":          f"WA-{e['wa_id']}",
            "type":        "wassenaar",
            "regime":      "wa",
            "label":       e["label"],
            "item_no":     e["wa_id"],
            "category":    e["category"],
            "category_name": e["category_name"],
            "control_type":  e["control_type"],
            "description": e.get("description", ""),
            "requirement_text": None,
            "is_sensitive":      e["is_sensitive"],
            "is_very_sensitive": e["is_very_sensitive"],
            "edges": {"explicit": [], "derived": [], "semantic": []},
            "source_refs": {"wassenaar_pdf": e["source_file"]},
        })
    return nodes
