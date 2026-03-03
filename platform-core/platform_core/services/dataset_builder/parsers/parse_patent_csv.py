"""特許 CSV パーサー。

対応フォーマット:
  1. J-PlatPat バッチダウンロード CSV（Shift-JIS / UTF-8）
  2. Google Patents BigQuery エクスポート CSV（UTF-8）
  3. Espacenet / Lens.org エクスポート CSV（UTF-8）
  4. 汎用フォールバック（列名を自動マッピング）

出力フォーマット (parse_patents.py の patent_index と同一構造):
  {
    "publication_number": "JP2023-123456",
    "title": "...",
    "assignee": "...",
    "abstract": "...",
    "fulltext": "",          # CSV では通常なし
    "ipc_codes_raw": "G03F/00;H01L/00",
    "source_url": "",
    "ingested_at": "ISO8601",
    "usecases": [],          # CSV 段階では空（ビルダーで用途推定）
    "_domain_tag": "",       # IPC から自動付与
    "_source_file": "filename.csv",
    "_source_format": "jplatpat|google_patents|espacenet|generic",
  }
"""

from __future__ import annotations

import csv
import io
import pathlib
import re
from datetime import datetime, timezone
from typing import Callable

# ── IPC → domain_tag マッピング ──────────────────────────────────
_IPC_TO_DOMAIN: list[tuple[str, str]] = [
    ("G03F",  "フォトリソグラフィー/レジスト・現像・膜"),
    ("H01L",  "半導体装置/計測・検査/制御"),
    ("C23C",  "成膜/エッチング/CVD-ALD/PVD/プラズマ"),
    ("H01S",  "先端材料/ガス/レーザ・光学/真空・低温"),
    ("C08F",  "先端材料/ガス/レーザ・光学/真空・低温"),
    ("C08G",  "先端材料/ガス/レーザ・光学/真空・低温"),
    ("C07C",  "先端材料/ガス/レーザ・光学/真空・低温"),
    ("B82Y",  "先端材料/ガス/レーザ・光学/真空・低温"),
    ("B81B",  "成膜/エッチング/CVD-ALD/PVD/プラズマ"),
    ("H04B",  "設計技術/EDA/ハードウェアセキュリティ/先端実装"),
    ("H04L",  "設計技術/EDA/ハードウェアセキュリティ/先端実装"),
    ("G06F",  "設計技術/EDA/ハードウェアセキュリティ/先端実装"),
    ("C25F",  "めっき/CMP/配線・コンタクト統合"),
    ("C25D",  "めっき/CMP/配線・コンタクト統合"),
]

def _ipc_to_domain(ipc_raw: str) -> str:
    if not ipc_raw:
        return ""
    for prefix, domain in _IPC_TO_DOMAIN:
        if prefix in ipc_raw:
            return domain
    return ""


def _normalize_ipc(raw: str) -> str:
    """IPC コード文字列をセミコロン区切りに正規化。
    入力例: "G03F 7/004; H01L 21/027" → "G03F/004;H01L/021"
    """
    if not raw:
        return ""
    parts = re.split(r"[,;\s]+", raw.strip())
    normalized = []
    for p in parts:
        p = p.strip()
        if re.match(r"^[A-HY]\d{2}[A-Z]", p):
            # スペースを / に変換
            p = re.sub(r"\s+", "/", p)
            normalized.append(p)
    return ";".join(normalized)


def _normalize_pub_no(raw: str, default_prefix: str = "") -> str:
    """公開番号を正規化する。"""
    raw = raw.strip().replace(" ", "").replace("-", "")
    if not raw:
        return ""
    # JP2023-123456 → JP2023123456
    return raw


def _detect_encoding(path: pathlib.Path) -> str:
    """ファイルのエンコーディングを簡易検出する。"""
    with path.open("rb") as f:
        raw = f.read(4096)
    # BOM チェック
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    # Shift-JIS 特徴バイト
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "shift_jis"


# ── フォーマット検出 ────────────────────────────────────────────

class _FormatSpec:
    """列名マッピングの仕様。"""
    def __init__(
        self,
        name:       str,
        pub_no:     list[str],
        title:      list[str],
        assignee:   list[str],
        abstract:   list[str],
        ipc:        list[str],
        fulltext:   list[str] | None = None,
        filing_date: list[str] | None = None,
    ):
        self.name        = name
        self.pub_no      = [c.lower() for c in pub_no]
        self.title       = [c.lower() for c in title]
        self.assignee    = [c.lower() for c in assignee]
        self.abstract    = [c.lower() for c in abstract]
        self.ipc         = [c.lower() for c in ipc]
        self.fulltext    = [c.lower() for c in (fulltext or [])]
        self.filing_date = [c.lower() for c in (filing_date or [])]


_FORMAT_SPECS: list[_FormatSpec] = [
    # ── J-PlatPat ──────────────────────────────────────────────
    _FormatSpec(
        name="jplatpat",
        pub_no=     ["公開番号", "出願番号", "公表番号", "再公表番号"],
        title=      ["発明の名称", "考案の名称", "タイトル", "名称"],
        assignee=   ["出願人", "権利者", "特許権者"],
        abstract=   ["要約", "要約書", "抄録"],
        ipc=        ["ipc分類", "国際特許分類", "ipc", "分類"],
        filing_date=["出願日", "国際出願日"],
    ),
    # ── Google Patents BigQuery ─────────────────────────────────
    _FormatSpec(
        name="google_patents",
        pub_no=     ["publication_number", "pub_number"],
        title=      ["title_ja", "title_localized", "title"],
        assignee=   ["assignee_harmonized", "assignee"],
        abstract=   ["abstract_ja", "abstract_localized", "abstract"],
        ipc=        ["ipc_code", "ipc_codes", "ipc"],
        fulltext=   ["claims_ja", "claims"],
        filing_date=["filing_date"],
    ),
    # ── Espacenet / Lens.org ────────────────────────────────────
    _FormatSpec(
        name="espacenet",
        pub_no=     ["publication number", "lens_id", "publication_number"],
        title=      ["title", "invention_title"],
        assignee=   ["applicant(s)", "applicants", "owner"],
        abstract=   ["abstract"],
        ipc=        ["ipc", "classification_ipc", "international classification"],
        filing_date=["date of filing", "filing_date"],
    ),
]


def _col_lower_map(headers: list[str]) -> dict[str, str]:
    """ヘッダー → 小文字マップ {小文字: 元の列名}。"""
    return {h.lower(): h for h in headers}


def _pick_col(col_lower: dict[str, str], candidates: list[str]) -> str | None:
    """candidates の中で最初にマッチした元の列名を返す。部分一致も許可。"""
    for cand in candidates:
        if cand in col_lower:
            return col_lower[cand]
    # 部分一致フォールバック
    for cand in candidates:
        for key, orig in col_lower.items():
            if cand in key:
                return orig
    return None


def _detect_format(headers: list[str]) -> _FormatSpec:
    """ヘッダーからフォーマットを判定する。"""
    col_lower = _col_lower_map(headers)
    best_spec   = _FORMAT_SPECS[-1]  # fallback = espacenet (最後)
    best_score  = 0

    for spec in _FORMAT_SPECS:
        score = 0
        for cand_list in [spec.pub_no, spec.title, spec.assignee, spec.abstract, spec.ipc]:
            if _pick_col(col_lower, cand_list):
                score += 1
        if score > best_score:
            best_score = score
            best_spec  = spec

    return best_spec


# ── 共通パース関数 ──────────────────────────────────────────────

def _parse_row(row: dict[str, str], spec: _FormatSpec, col_lower: dict[str, str]) -> dict | None:
    """1行を内部形式に変換する。失敗時は None を返す。"""
    def get(candidates: list[str]) -> str:
        col = _pick_col(col_lower, candidates)
        return (row.get(col, "") or "").strip() if col else ""

    pub_no   = _normalize_pub_no(get(spec.pub_no))
    title    = get(spec.title)
    if not pub_no and not title:
        return None

    assignee  = get(spec.assignee)
    abstract  = get(spec.abstract)
    ipc_raw   = _normalize_ipc(get(spec.ipc))
    fulltext  = get(spec.fulltext) if spec.fulltext else ""
    filing    = get(spec.filing_date) if spec.filing_date else ""

    # pub_no がない場合は title から生成
    if not pub_no:
        pub_no = f"UNKNOWN-{hash(title) & 0xFFFFFF:06X}"

    domain_tag = _ipc_to_domain(ipc_raw)

    return {
        "publication_number": pub_no,
        "title":              title,
        "assignee":           assignee,
        "abstract":           abstract,
        "fulltext":           fulltext,
        "ipc_codes_raw":      ipc_raw,
        "source_url":         "",
        "filing_date":        filing,
        "ingested_at":        datetime.now(timezone.utc).isoformat(),
        "usecases":           [],
        "_domain_tag":        domain_tag,
    }


def parse_patent_csv(
    csv_path: pathlib.Path,
    source_file: str | None = None,
) -> tuple[list[dict], dict]:
    """特許 CSV ファイルを読み込み内部形式のレコードリストを返す。

    Returns:
        (records, meta)
        records: 各特許の内部形式 dict のリスト
        meta: {"format": "jplatpat", "total": N, "skipped": M, ...}
    """
    encoding = _detect_encoding(csv_path)
    source_file = source_file or csv_path.name

    with csv_path.open(encoding=encoding, errors="replace", newline="") as f:
        # BOM 除去
        content = f.read()
        if content.startswith("\ufeff"):
            content = content[1:]

    reader = csv.DictReader(io.StringIO(content))
    headers = reader.fieldnames or []
    spec    = _detect_format(list(headers))
    col_lower = _col_lower_map(list(headers))

    records: list[dict] = []
    skipped = 0

    for row in reader:
        r = _parse_row(row, spec, col_lower)
        if r is None:
            skipped += 1
            continue
        r["_source_file"]   = source_file
        r["_source_format"] = spec.name
        records.append(r)

    meta = {
        "format":   spec.name,
        "encoding": encoding,
        "total":    len(records) + skipped,
        "parsed":   len(records),
        "skipped":  skipped,
    }
    return records, meta
