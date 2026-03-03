"""特許 CSV インジェストパイプライン。

raw/ の CSV を読み込み → 正規化 → processed/*.jsonl に書き出す。
builder.py はビルド時に processed/*.jsonl をマージして patent_index を生成する。

処理フロー:
  data/source/patents/raw/*.csv
      → parse_patent_csv() × N
      → 重複除去（publication_number）
      → data/source/patents/processed/patents_ingested.jsonl
      → data/source/patents/processed/ingest_report.json
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone
from typing import Any

from .parsers.parse_patent_csv import parse_patent_csv

_PROJECT_ROOT   = pathlib.Path(__file__).parent.parent.parent.parent.parent
_RAW_DIR        = _PROJECT_ROOT / "data" / "source" / "patents" / "raw"
_PROCESSED_DIR  = _PROJECT_ROOT / "data" / "source" / "patents" / "processed"
_OUTPUT_JSONL   = _PROCESSED_DIR / "patents_ingested.jsonl"
_REPORT_PATH    = _PROCESSED_DIR / "ingest_report.json"


def run_ingest(raw_dir: pathlib.Path | None = None) -> dict[str, Any]:
    """raw/ の全 CSV を処理して processed/patents_ingested.jsonl に書き出す。

    Returns:
        report dict（ファイル別統計 + 合計）
    """
    raw_dir = raw_dir or _RAW_DIR
    _PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    csv_files = sorted(f for f in raw_dir.iterdir()
                       if f.is_file() and f.suffix.lower() in {".csv", ".tsv"})

    if not csv_files:
        return {
            "status":  "no_files",
            "message": f"CSV ファイルが {raw_dir} に見つかりません",
            "files":   [],
        }

    all_records: dict[str, dict] = {}   # pub_no → record（重複除去）
    file_reports: list[dict] = []
    errors: list[dict] = []

    for csv_path in csv_files:
        try:
            records, meta = parse_patent_csv(csv_path)
            dup_count = 0
            for r in records:
                pub_no = r["publication_number"]
                if pub_no in all_records:
                    dup_count += 1
                else:
                    all_records[pub_no] = r
            file_reports.append({
                "file":    csv_path.name,
                "format":  meta["format"],
                "encoding": meta["encoding"],
                "parsed":  meta["parsed"],
                "skipped": meta["skipped"],
                "duplicate_skipped": dup_count,
            })
        except Exception as exc:
            errors.append({"file": csv_path.name, "error": str(exc)})

    # 書き出し
    with _OUTPUT_JSONL.open("w", encoding="utf-8") as f:
        for record in all_records.values():
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    report: dict[str, Any] = {
        "status":      "ok",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_unique": len(all_records),
        "files":       file_reports,
        "errors":      errors,
    }

    with _REPORT_PATH.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return report


def load_ingested_patents() -> list[dict]:
    """processed/patents_ingested.jsonl を読み込んで返す。"""
    if not _OUTPUT_JSONL.exists():
        return []
    records = []
    with _OUTPUT_JSONL.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records
