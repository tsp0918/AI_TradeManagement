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
        "intersection": "★ 規制合致",
        "core_only":    "直接一致",
        "expanded_only": "用途関連",
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
        f"★ 規制合致 {c.get('intersection', 0)} 件 / "
        f"直接一致 {c.get('core_only', 0)} 件 / "
        f"用途関連 {c.get('expanded_only', 0)} 件",
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
        "用途関連件数",
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

def _extract_tx_summary(tx: Any) -> Dict[str, Any]:
    """
    Transaction から取引概要情報を抽出する。
    - item_name / item_model : TransactionItem の品目名・型番
    - core_usage             : source='core' の申告用途テキスト
    - bom_items              : BOM 主要構成品（上位5件）
    - source_module          : 登録元モジュール
    """
    import json as _json

    item_name = ""
    item_model = ""
    bom_items: List[Dict[str, str]] = []
    core_usage = ""

    # TransactionItem（品目情報）
    items = getattr(tx, "items", []) or []
    if items:
        first = items[0]
        item_name  = getattr(first, "item_name", "") or ""
        item_model = getattr(first, "item_model", "") or ""
        spec_text  = getattr(first, "spec_text", "") or ""
        # spec_text から bom_json を抽出（ai_classification が埋め込む形式）
        try:
            for line in spec_text.splitlines():
                if line.startswith("bom_json:"):
                    pass
            # "bom_json:\n[{...}]" の形式を探してパース
            import re
            m = re.search(r"bom_json:\s*(\[.*?\])", spec_text, re.DOTALL)
            if m:
                raw = _json.loads(m.group(1))
                for b in raw[:5]:
                    if b.get("kind") == "material":
                        bom_items.append({
                            "code": b.get("component_code", ""),
                            "name": b.get("component_name", ""),
                            "desc": b.get("description", ""),
                            "coo":  b.get("coo", ""),
                        })
        except Exception:
            pass

    # core UsageRequirement
    urs = getattr(tx, "usage_requirements", []) or []
    for ur in urs:
        if getattr(ur, "source", "") == "core":
            core_usage = (getattr(ur, "text", "") or "").strip()
            break

    _MODULE_LABEL = {
        "ai_classification": "品目管理",
        "rnd_assessment":    "R&D案件管理",
        "manual":            "手動登録",
    }
    source_label = _MODULE_LABEL.get(
        getattr(tx, "source_module", "") or "", getattr(tx, "source_module", "") or "不明"
    )

    return {
        "item_name":    item_name,
        "item_model":   item_model,
        "core_usage":   core_usage,
        "bom_items":    bom_items,
        "source_label": source_label,
    }


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
        tx_summary=_extract_tx_summary(tx),
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
