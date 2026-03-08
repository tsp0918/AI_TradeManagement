"""BIS Commerce Control List (ECCN) PDF パーサー。

15 CFR Part 774 の PDF から ECCN エントリーを抽出する。

出力フォーマット:
  {
    "eccn":        "3E001",
    "label":       "Technology for the 'production'...",
    "controls":    ["NS", "AT"],  # Reason for Control
    "description": "...",         # 最初の説明文
    "source_file": "Part 774.pdf",
  }

使用方法:
    from .parsers.parse_eccn_pdf import parse_eccn_pdf
    entries = parse_eccn_pdf(path)
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

# pdfplumber を優先、なければ PyPDF2
try:
    import pdfplumber
    _BACKEND = "pdfplumber"
except ImportError:
    try:
        import PyPDF2  # noqa: F401
        _BACKEND = "pypdf2"
    except ImportError:
        _BACKEND = None


# ── ECCN コードのパターン ───────────────────────────────────────
# 例: 0A501, 3E001, 7A994, EAR99
_ECCN_HEADER_RE = re.compile(
    r"^([0-9][A-E][0-9]{3}(?:\.[a-z])?)\s+(.*)",
    re.MULTILINE,
)

# Reason for Control 行
_REASON_RE = re.compile(r"Reason for Control:\s*([A-Z,\s]+)", re.IGNORECASE)

# 制御カテゴリ略語
_CONTROL_CODES = {
    "NS": "National Security",
    "MT": "Missile Technology",
    "NP": "Nuclear Nonproliferation",
    "CB": "Chemical & Biological",
    "RS": "Regional Stability",
    "AT": "Anti-Terrorism",
    "FC": "Firearms",
    "CC": "Crime Control",
    "EI": "Encryption",
    "UN": "United Nations",
    "SS": "Surreptitious Listening",
}

# 半導体・電子関連の ECCN カテゴリ (3xxx = Electronics, 2xxx = Materials)
_PRIORITY_PREFIXES = {"3", "2", "7", "4", "5", "6", "0"}


def _extract_pages_text(pdf_path: pathlib.Path) -> list[str]:
    """PDF の全ページテキストをリストで返す。"""
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
        raise RuntimeError("PDF ライブラリが見つかりません (pdfplumber or PyPDF2)")


def parse_eccn_pdf(
    pdf_path: pathlib.Path | str,
    max_pages: int | None = None,
) -> list[dict[str, Any]]:
    """BIS CCL PDF から ECCN エントリーを抽出する。

    Args:
        pdf_path:  Part 774 PDF のパス
        max_pages: 処理する最大ページ数（None = 全ページ）

    Returns:
        ECCN エントリーの list
    """
    pdf_path = pathlib.Path(pdf_path)
    source_file = pdf_path.name

    pages = _extract_pages_text(pdf_path)
    if max_pages:
        pages = pages[:max_pages]

    # ── 全ページテキストを結合してエントリーを抽出 ──────────────
    # ECCN ヘッダー → 次のヘッダーまでのブロックを1エントリーとして処理
    full_text = "\n".join(pages)

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    # ページヘッダー / フッターのノイズを除去
    # "15 CFR Part 774 (up to date as of..." などを除去
    full_text = re.sub(r"15 CFR Part 774 \([^\n]*\)\n", "", full_text)
    full_text = re.sub(r"15 CFR 774\.\d+[^\n]*\n", "", full_text)
    full_text = re.sub(r"The Commerce Control List\n", "", full_text)
    full_text = re.sub(r"15 CFR 774\.\d+ \(enhanced display\)[^\n]*\n", "", full_text)

    # ECCN ヘッダー行を全て検索
    for m in _ECCN_HEADER_RE.finditer(full_text):
        eccn = m.group(1).split(".")[0]  # サブ項目 (0A501.a) はベース (0A501) に統合
        if eccn in seen:
            continue
        seen.add(eccn)

        label = m.group(2).strip()
        # 次のECCNまでのブロックを取得（最大500文字）
        start  = m.start()
        # 次の ECCN ヘッダー位置を探す
        next_m = _ECCN_HEADER_RE.search(full_text, m.end())
        end    = next_m.start() if next_m else start + 1500
        block  = full_text[start:min(end, start + 1500)]

        # Reason for Control を抽出
        controls: list[str] = []
        reason_m = _REASON_RE.search(block)
        if reason_m:
            raw_controls = reason_m.group(1)
            controls = [c.strip() for c in re.split(r"[,\s]+", raw_controls)
                        if c.strip() in _CONTROL_CODES]

        # 先頭の説明文を取得（ラベル行の次の段落）
        description_lines: list[str] = []
        for line in block.split("\n")[1:6]:
            stripped = line.strip()
            if not stripped:
                break
            if re.match(r"^LICENSE REQUIREMENTS|^Reason for Control:|^Country", stripped):
                break
            description_lines.append(stripped)
        description = " ".join(description_lines)[:300]

        entries.append({
            "eccn":        eccn,
            "label":       label[:200],
            "controls":    controls,
            "description": description,
            "source_file": source_file,
        })

    return entries


def enrich_eccn_nodes(
    nodes: list[dict[str, Any]],
    eccn_entries: list[dict[str, Any]],
) -> int:
    """ECCN stub nodes を BIS CCL の情報で補強する。

    サブ項目コード（例: 1C350(b)）は親コード（1C350）にフォールバックして補強する。

    Returns:
        補強したノード数
    """
    import re as _re

    eccn_index = {e["eccn"]: e for e in eccn_entries}
    enriched = 0
    for node in nodes:
        if node.get("type") != "eccn":
            continue
        eccn = node.get("item_no", "") or node.get("id", "")

        # 1. 完全一致
        entry = eccn_index.get(eccn)
        sub_para = ""

        # 2. フォールバック: 括弧付きサブ項目 "1C350(b)" → 親コード "1C350"
        if not entry:
            base_code = _re.sub(r'\([^)]*\)\s*$', '', eccn).strip()
            entry = eccn_index.get(base_code)
            if entry:
                sub_para = eccn[len(base_code):].strip()  # "(b)" など

        if not entry:
            continue

        prefix = f"[{sub_para}] " if sub_para else ""
        if entry.get("label"):
            node["description"] = prefix + entry["label"]
        if entry.get("controls"):
            node["bis_controls"] = entry["controls"]
        if entry.get("description"):
            node["bis_description"] = entry["description"]
        node["source_refs"]["eccn_pdf"] = entry["source_file"]
        enriched += 1
    return enriched
