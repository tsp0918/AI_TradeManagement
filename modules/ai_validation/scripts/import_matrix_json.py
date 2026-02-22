# scripts/import_matrix_json.py
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# ---------------------------------------------------------
# ✅ 先に import path を通す（これが重要）
# ---------------------------------------------------------
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.db.session import SessionLocal  # noqa: E402
from app.db.models.matrix import MatrixRule  # noqa: E402


def _s(x: Any) -> str:
    """safe string"""
    if x is None:
        return ""
    if isinstance(x, str):
        return x.strip()
    return str(x).strip()


def _first_nonempty(*vals: Any) -> str:
    for v in vals:
        s = _s(v)
        if s:
            return s
    return ""


def _to_date(x: Any) -> Optional[str]:
    """
    effective_date を入れたい場合に備えた軽い変換（今は使わなくてもOK）
    SQLiteなら文字列でも入る設計にしている前提。
    """
    s = _s(x)
    return s or None


def _normalize_root(data: Any) -> Any:
    """
    ここで「root.data がある形式」「dataが無い形式」を吸収。
    """
    if isinstance(data, dict) and "data" in data and isinstance(data["data"], (dict, list)):
        return data["data"]
    return data


def _iter_rules_from_fx_matrix(data: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """
    旧形式（あなたが貼ってくれた構造）:
      {
        "schema_version": "...",
        "source": {...},
        "data": {
          "sheet": "...",
          "export_items": [
            {
              "export_order_ref": "...",
              "export_order_item": "...",
              "cargo_rules": [
                {
                  "meti_order_ref": "...",
                  "definition": "...",
                  "term": "...",
                  "term_meaning": "...",
                  "notes_or_exclusions": "...",
                  "eccn": "...",
                  "substances": [...]
                }
              ]
            }
          ]
        }
      }

    もしくは data が無い版:
      {
        "sheet": "...",
        "export_items": [...]
      }
    """
    root = _normalize_root(data)
    if not isinstance(root, dict):
        raise ValueError("FX matrix JSONは dict を期待しています。")

    sheet_name = _s(root.get("sheet"))
    export_items = root.get("export_items") or []
    if not isinstance(export_items, list):
        raise ValueError("export_items が list ではありません。")

    for ex in export_items:
        if not isinstance(ex, dict):
            continue

        export_order_ref = _s(ex.get("export_order_ref"))
        export_order_item = _s(ex.get("export_order_item"))
        cargo_rules = ex.get("cargo_rules") or []

        # cargo_rules が空の場合：輸出令の行だけ登録したいなら入れる
        if not cargo_rules:
            if export_order_item:
                item_no = _first_nonempty(export_order_ref, export_order_item[:120])
                title = export_order_item
                requirement_text = export_order_item  # NOT NULL対策（最低限ここを入れる）
                yield {
                    "regime": "JP_FX",
                    "list_name": sheet_name or None,
                    "item_no": item_no,
                    "title": title,
                    "requirement_text": requirement_text,
                    "usage_criteria_text": None,
                    "tech_criteria_text": None,
                    "notes": None,
                    "version": None,
                    "effective_date": None,
                }
            continue

        # cargo_rules がある場合：1 cargo_rule = 1 MatrixRule
        if not isinstance(cargo_rules, list):
            continue

        for cr in cargo_rules:
            if not isinstance(cr, dict):
                continue

            meti_order_ref = _s(cr.get("meti_order_ref"))
            definition = _s(cr.get("definition"))
            term = _s(cr.get("term"))
            term_meaning = _s(cr.get("term_meaning"))
            notes_excl = _s(cr.get("notes_or_exclusions"))
            eccn = _s(cr.get("eccn"))
            substances = cr.get("substances") or []

            # title（表示名）
            title = _first_nonempty(export_order_item, term, export_order_ref, meti_order_ref, "rule")

            # requirement_text（NOT NULL 必須）
            requirement_text = _first_nonempty(definition, export_order_item, term, title, "N/A")

            # item_no（識別子）
            parts: List[str] = []
            if export_order_ref:
                parts.append(export_order_ref.replace("\n", " ").strip())
            if meti_order_ref:
                parts.append(meti_order_ref.replace("\n", " ").strip())
            item_no = " / ".join(parts) or title[:160]

            # usage_criteria_text（用語定義など）
            usage_criteria_text = None
            if term or term_meaning:
                usage_criteria_text = "\n".join([x for x in [term, term_meaning] if x]).strip() or None

            # tech_criteria_text（ECCNや物質リスト）
            tech_lines: List[str] = []
            if eccn:
                tech_lines.append(f"ECCN: {eccn}")
            if isinstance(substances, list) and substances:
                tech_lines.append("Substances:")
                tech_lines.extend([f"- {_s(s)}" for s in substances if _s(s)])
            tech_criteria_text = "\n".join(tech_lines).strip() or None

            yield {
                "regime": "JP_FX",
                "list_name": sheet_name or None,
                "item_no": item_no,
                "title": title,
                "requirement_text": requirement_text,
                "usage_criteria_text": usage_criteria_text,
                "tech_criteria_text": tech_criteria_text,
                "notes": notes_excl or None,
                "version": None,
                "effective_date": None,
            }


def _iter_rules_from_normalized(data: Any) -> Iterable[Dict[str, Any]]:
    """
    normalized JSON 側（想定）:
      - list: [{...rule...}, ...]
      - dict: {"rules": [...]} / {"items":[...]} / {"matrix_rules":[...]} など
      - dict: すでに1件の rule dict
    で来ても吸収する。
    """
    root = _normalize_root(data)

    # list 直
    if isinstance(root, list):
        for r in root:
            if isinstance(r, dict):
                yield r
        return

    if not isinstance(root, dict):
        raise ValueError("normalized JSONは dict または list を期待しています。")

    for key in ("rules", "items", "matrix_rules", "matrixRules"):
        if key in root and isinstance(root[key], list):
            for r in root[key]:
                if isinstance(r, dict):
                    yield r
            return

    # 1件 dict 直
    yield root


def _coerce_rule(r: Dict[str, Any]) -> Dict[str, Any]:
    """
    normalized 側のキー揺れを吸収して MatrixRule に入る形に整形する。
    requirement_text を必ず埋める。
    """
    regime = _first_nonempty(r.get("regime"), r.get("law"), r.get("法令"), "JP_FX")
    list_name = r.get("list_name") or r.get("listName") or r.get("sheet") or r.get("list") or None
    item_no = _first_nonempty(r.get("item_no"), r.get("itemNo"), r.get("番号"), r.get("項番"))
    title = _first_nonempty(r.get("title"), r.get("name"), r.get("名称"), "")
    requirement_text = _first_nonempty(
        r.get("requirement_text"),
        r.get("requirementText"),
        r.get("text"),
        r.get("body"),
        r.get("definition"),
        title,
        item_no,
        "N/A",
    )
    usage_criteria_text = _first_nonempty(r.get("usage_criteria_text"), r.get("usageCriteriaText")) or None
    tech_criteria_text = _first_nonempty(r.get("tech_criteria_text"), r.get("techCriteriaText")) or None
    notes = _first_nonempty(r.get("notes"), r.get("notes_or_exclusions"), r.get("note")) or None
    version = _first_nonempty(r.get("version"), r.get("rev"), r.get("改訂")) or None
    effective_date = _to_date(r.get("effective_date") or r.get("effectiveDate"))

    if not item_no:
        # item_no はユニークキーに使うので無いものは落とす
        raise ValueError("item_no が見つからないルールがありました（normalized形式）。")

    return {
        "regime": regime,
        "list_name": _s(list_name) if list_name else None,
        "item_no": item_no,
        "title": title,
        "requirement_text": requirement_text,
        "usage_criteria_text": usage_criteria_text,
        "tech_criteria_text": tech_criteria_text,
        "notes": notes,
        "version": version,
        "effective_date": effective_date,
    }


def _detect_and_iter_rules(data: Any) -> Iterable[Dict[str, Any]]:
    """
    JSON構造を見て、fx_matrix形式か normalized形式かを自動判別。
    """
    root = _normalize_root(data)

    # fx_matrix の特徴: dict かつ export_items がある
    if isinstance(root, dict) and isinstance(root.get("export_items"), list):
        yield from _iter_rules_from_fx_matrix(data)  # data を渡して data 有無を吸収
        return

    # normalized にフォールバック
    yield from _iter_rules_from_normalized(data)


def import_matrix(json_path: Path, purge: bool = False) -> int:
    data = json.loads(json_path.read_text(encoding="utf-8"))

    raw_rules = list(_detect_and_iter_rules(data))
    if not raw_rules:
        raise ValueError("JSONからルールが1件も取れませんでした。")

    db = SessionLocal()
    try:
        if purge:
            db.query(MatrixRule).delete()
            db.commit()

        n = 0
        for raw in raw_rules:
            # fx_matrix 形式はすでに整形済み、normalized は coerce
            if "requirement_text" in raw and "item_no" in raw and "regime" in raw:
                rule = raw
            else:
                rule = _coerce_rule(raw)

            # upsert: regime + item_no + version（versionが無い運用でもOK）
            q = db.query(MatrixRule).filter(
                MatrixRule.regime == rule["regime"],
                MatrixRule.item_no == rule["item_no"],
            )
            if hasattr(MatrixRule, "version"):
                q = q.filter(MatrixRule.version == rule.get("version"))

            obj = q.first()
            if not obj:
                obj = MatrixRule(
                    regime=rule["regime"],
                    item_no=rule["item_no"],
                    version=rule.get("version"),
                )

            # columns
            if hasattr(obj, "list_name"):
                obj.list_name = rule.get("list_name")
            obj.title = rule.get("title") or ""
            obj.requirement_text = rule.get("requirement_text") or "N/A"  # ✅ NOT NULL 対策
            if hasattr(obj, "usage_criteria_text"):
                obj.usage_criteria_text = rule.get("usage_criteria_text")
            if hasattr(obj, "tech_criteria_text"):
                obj.tech_criteria_text = rule.get("tech_criteria_text")
            if hasattr(obj, "notes"):
                obj.notes = rule.get("notes")
            if hasattr(obj, "effective_date"):
                obj.effective_date = rule.get("effective_date")

            # timestamps（モデル側で自動なら無視されてもOK）
            if hasattr(obj, "updated_at"):
                obj.updated_at = datetime.utcnow()

            db.add(obj)
            n += 1

        db.commit()
        return n
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path", type=str, help="path to matrix.json")
    ap.add_argument("--purge", action="store_true", help="delete existing matrix_rules first")
    args = ap.parse_args()

    p = Path(args.json_path)
    if not p.exists():
        raise SystemExit(f"File not found: {p}")

    cnt = import_matrix(p, purge=args.purge)
    print(f"Imported/Updated MatrixRule: {cnt}")


if __name__ == "__main__":
    main()
