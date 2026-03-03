# app/services/export_report.py
"""
該非判定レポートの CSV / PDF 生成サービス。

  build_csv(tx, two_lists, run_at)  -> str (CSV テキスト)
  build_pdf(tx, two_lists, run_at, templates) -> bytes (PDF バイナリ)
"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any, Dict, List, Optional

# WeasyPrint は起動時ではなく、呼び出し時のみインポート（重いため）
# from weasyprint import HTML  ← 下の build_pdf 内でインポート


# ──────────────────────────────────────────────
# 共通ヘルパー
# ──────────────────────────────────────────────

def _fmt_score(v: Any) -> str:
    try:
        return f"{float(v):.3f}"
    except (TypeError, ValueError):
        return "-"


def _category_label(cat: str) -> str:
    return {
        "intersection": "両方一致",
        "core_only":    "直接一致のみ",
        "expanded_only": "特許示唆のみ",
    }.get(cat, cat)


def _row_label(item: Dict[str, Any]) -> str:
    ids = item.get("item_ids") or []
    if ids:
        return " / ".join(ids)
    return (item.get("item_label") or "").strip()[:80]


def _collect_rows(two_lists: Dict[str, Any]) -> List[Dict[str, Any]]:
    """intersection → core_only → expanded_only の順に全行をフラット化"""
    out = []
    for cat in ("intersection", "core_only", "expanded_only"):
        for item in two_lists.get(cat, []):
            out.append({**item, "_category": cat})
    return out


# ──────────────────────────────────────────────
# CSV
# ──────────────────────────────────────────────

def build_csv(
    tx: Any,
    two_lists: Dict[str, Any],
    run_at: Optional[datetime] = None,
) -> str:
    """
    BOM付き UTF-8 の CSV 文字列を返す（Excel で直接開ける）。

    構成:
      [A] 案件情報ヘッダー（3行）
      [B] 空行
      [C] 判定結果テーブル（ヘッダー + データ行）
    """
    buf = io.StringIO()
    # BOM を先頭に付ける（Excel UTF-8 対応）
    buf.write("\ufeff")
    w = csv.writer(buf, lineterminator="\r\n")

    # ── [A] 案件情報 ──
    run_at_str = run_at.strftime("%Y-%m-%d %H:%M") if run_at else "-"
    c = two_lists.get("counts", {})
    w.writerow(["【該非判定レポート】"])
    w.writerow(["出力日時", datetime.now().strftime("%Y-%m-%d %H:%M")])
    w.writerow(["案件番号", tx.case_no or "-"])
    w.writerow(["タイトル", tx.title or "-"])
    w.writerow(["取引先", getattr(tx, "counterparty_name", None) or "-"])
    w.writerow(["ステータス", tx.status or "-"])
    w.writerow(["最終AI解析", run_at_str])
    w.writerow([
        "判定サマリー",
        f"両方一致 {c.get('intersection', 0)} 件 / "
        f"直接一致のみ {c.get('core_only', 0)} 件 / "
        f"特許示唆のみ {c.get('expanded_only', 0)} 件",
    ])
    w.writerow([])  # 空行

    # ── [C] 判定結果テーブル ──
    w.writerow([
        "判定区分",
        "規制番号",
        "リスト名",
        "規制タイトル",
        "規制要件（冒頭200字）",
        "最高判定",
        "最高スコア",
        "直接一致件数",
        "特許示唆件数",
        "主要一致語",
    ])

    for row in _collect_rows(two_lists):
        cat   = row["_category"]
        hits  = row.get("hits", {})
        core_hits = hits.get("core", [])
        exp_hits  = hits.get("expanded", [])

        # 主要一致語（最初の hit から）
        primary = (core_hits or exp_hits or [{}])[0]
        matched = primary.get("matched_compact") or []
        tokens_str = " / ".join(matched[:6]) if matched else "-"

        rule_summary = (row.get("rule_summary") or "").strip()

        w.writerow([
            _category_label(cat),
            _row_label(row),
            row.get("list_name") or "-",
            (row.get("title") or "").strip(),
            rule_summary[:200],
            row.get("best_decision") or "-",
            _fmt_score(row.get("max_score")),
            len(core_hits),
            len(exp_hits),
            tokens_str,
        ])

    return buf.getvalue()


# ──────────────────────────────────────────────
# PDF（WeasyPrint + Jinja2 テンプレート）
# ──────────────────────────────────────────────

def build_pdf(
    tx: Any,
    two_lists: Dict[str, Any],
    run_at: Optional[datetime],
    templates: Any,  # Jinja2 Templates (starlette)
) -> bytes:
    """
    Jinja2 で HTML を生成し WeasyPrint で PDF に変換して返す。
    """
    from weasyprint import HTML  # 遅延インポート

    c = two_lists.get("counts", {})
    run_at_str = run_at.strftime("%Y-%m-%d %H:%M") if run_at else "-"

    html_str = templates.get_template("report_pdf.html").render(
        tx=tx,
        two_lists=two_lists,
        counts=c,
        run_at_str=run_at_str,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        rows=_collect_rows(two_lists),
        category_label=_category_label,
        row_label=_row_label,
        fmt_score=_fmt_score,
    )

    pdf_bytes = HTML(string=html_str, base_url=None).write_pdf()
    return pdf_bytes
