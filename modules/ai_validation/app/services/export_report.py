# app/services/export_report.py
"""
該非判定レポートの CSV / PDF 生成サービス。

  build_csv(tx, two_lists, run_at)  -> str (CSV テキスト)
  build_pdf(tx, two_lists, run_at, templates, catchall_judgment) -> bytes (PDF バイナリ)
"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from platform_core.ontology.models.catchall import CatchallJudgment

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


def _build_executive_summary(two_lists: Dict[str, Any], tx: Any) -> Dict[str, Any]:
    """
    エグゼクティブサマリー用データを生成する。
    - risk_level: HIGH / MEDIUM / LOW
    - verdict_text: 判定文
    - top_items: 上位3規制項目（intersection 優先）
    - recommended_actions: 推奨アクションリスト
    - overall_label: 総合ラベル（OUTPUT用）
    """
    c = two_lists.get("counts", {})
    intersection = c.get("intersection", 0)
    core_only    = c.get("core_only", 0)
    expanded     = c.get("expanded_only", 0)

    if intersection > 0:
        risk_level = "HIGH"
        overall_label = "要許可確認"
        verdict_text = (
            f"申告用途・応用展開用途の両面で {intersection} 件の規制合致が検出されました。"
            " 輸出許可要否の確認が必要です。"
        )
        recommended_actions = [
            "輸出許可要件（外為法 / EAR）の詳細確認",
            "該当規制項番について安全保障貿易管理部門へ照会",
            "取引先の最終需要者確認（EUC）実施",
            "必要に応じ輸出許可申請の準備",
        ]
    elif core_only > 0:
        risk_level = "MEDIUM"
        overall_label = "要精査"
        verdict_text = (
            f"申告用途テキストが {core_only} 件の規制項目に直接一致しました。"
            " 専門家による詳細精査を推奨します。"
        )
        recommended_actions = [
            "一致規制項番のスペック要件と品目仕様の詳細照合",
            "品目の技術パラメータ（性能値・閾値）確認",
            "貿易管理担当者によるレビュー実施",
        ]
    else:
        risk_level = "LOW"
        overall_label = "低リスク"
        verdict_text = (
            "申告用途・応用展開用途において規制合致は検出されませんでした。"
            f" 用途関連項目 {expanded} 件を参考情報として付録に掲載します。"
        )
        recommended_actions = [
            "本判定結果をファイリングし輸出管理記録として保管",
            "用途変更が生じた場合は再判定を実施",
        ]

    # 上位項目（intersection → core_only → expanded_only の優先順）
    top_candidates: List[Dict[str, Any]] = []
    for cat in ("intersection", "core_only", "expanded_only"):
        for item in two_lists.get(cat, []):
            top_candidates.append({**item, "_category": cat})
            if len(top_candidates) >= 3:
                break
        if len(top_candidates) >= 3:
            break

    top_items = []
    for item in top_candidates:
        core_hits = (item.get("hits") or {}).get("core", [])
        exp_hits  = (item.get("hits") or {}).get("expanded", [])
        first     = (core_hits or exp_hits or [{}])[0]
        matched   = first.get("matched_compact") or []
        top_items.append({
            "label":    _row_label(item),
            "title":    (item.get("title") or "")[:50],
            "category": _category_label(item["_category"]),
            "score":    _fmt_score(item.get("max_score")),
            "tokens":   matched[:4],
        })

    return {
        "risk_level":           risk_level,
        "overall_label":        overall_label,
        "verdict_text":         verdict_text,
        "recommended_actions":  recommended_actions,
        "top_items":            top_items,
        "intersection":         intersection,
        "core_only":            core_only,
        "expanded_only":        expanded,
    }


def build_pdf(
    tx: Any,
    two_lists: Dict[str, Any],
    run_at: Optional[datetime],
    templates: Any,  # Jinja2 Templates (starlette)
    catchall_judgment: Optional["CatchallJudgment"] = None,
) -> bytes:
    """
    Jinja2 で HTML を生成し WeasyPrint で PDF に変換して返す。

    catchall_judgment が渡された場合、PDF 末尾にキャッチオール自己判定セクションを追加する。
    """
    from weasyprint import HTML  # 遅延インポート

    c = two_lists.get("counts", {})
    run_at_str = run_at.strftime("%Y-%m-%d %H:%M") if run_at else "-"

    catchall_dict = catchall_judgment.to_dict() if catchall_judgment else None

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
        executive_summary=_build_executive_summary(two_lists, tx),
        catchall=catchall_dict,
    )

    pdf_bytes = HTML(string=html_str, base_url=None).write_pdf()
    return pdf_bytes
