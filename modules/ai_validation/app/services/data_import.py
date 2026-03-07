# app/services/data_import.py
"""
matrix.json / patents.json を DB に投入するサービス関数。
既存スクリプト (scripts/import_matrix_json.py, scripts/import_patents_json.py) の
コアロジックを Session を受け取る形式に移植している。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from app.db.models.matrix import MatrixRule
from app.db.models.patent import Patent, PatentUsecase


# ---------------------------------------------------------------------------
# 共通ユーティリティ
# ---------------------------------------------------------------------------

def _s(x: Any) -> str:
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
    s = _s(x)
    return s or None


def _normalize_root(data: Any) -> Any:
    if isinstance(data, dict) and "data" in data and isinstance(data["data"], (dict, list)):
        return data["data"]
    return data


def _get_ref_str(v: Any, prefer: str = "norm") -> str:
    """plain文字列または {"raw":..., "norm":..., "id":...} オブジェクトから文字列を取り出す。"""
    if not v:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        return _s(v.get(prefer) or v.get("raw") or v.get("id") or "")
    return str(v).strip()


def _get_ref_id(v: Any) -> str:
    """ref オブジェクトの id フィールドを取り出す。"""
    if isinstance(v, dict):
        return _s(v.get("id") or "")
    if isinstance(v, str):
        return v.strip()
    return ""


def _get_substance_text(s: Any) -> str:
    """物質エントリ（文字列または {"raw":..., "text":...} オブジェクト）からテキストを取り出す。"""
    if isinstance(s, str):
        return s.strip()
    if isinstance(s, dict):
        return _s(s.get("text") or s.get("raw") or "")
    return ""


# ---------------------------------------------------------------------------
# MatrixRule インポート
# ---------------------------------------------------------------------------

def _iter_rules_from_fx_matrix(data: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
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

        if not cargo_rules:
            if export_order_item:
                item_no = _first_nonempty(export_order_ref, export_order_item[:120])
                yield {
                    "regime": "JP_FX",
                    "list_name": sheet_name or None,
                    "item_no": item_no,
                    "title": export_order_item,
                    "requirement_text": export_order_item,
                    "usage_criteria_text": None,
                    "tech_criteria_text": None,
                    "notes": None,
                    "version": None,
                    "effective_date": None,
                }
            continue

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

            title = _first_nonempty(export_order_item, term, export_order_ref, meti_order_ref, "rule")
            requirement_text = _first_nonempty(definition, export_order_item, term, title, "N/A")

            parts: List[str] = []
            if export_order_ref:
                parts.append(export_order_ref.replace("\n", " ").strip())
            if meti_order_ref:
                parts.append(meti_order_ref.replace("\n", " ").strip())
            item_no = " / ".join(parts) or title[:160]

            usage_criteria_text = None
            if term or term_meaning:
                usage_criteria_text = "\n".join([x for x in [term, term_meaning] if x]).strip() or None

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
    root = _normalize_root(data)

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

    yield root


def _coerce_rule(r: Dict[str, Any]) -> Dict[str, Any]:
    regime = _first_nonempty(r.get("regime"), r.get("law"), r.get("法令"), "JP_FX")
    list_name = r.get("list_name") or r.get("listName") or r.get("sheet") or r.get("list") or None
    item_no = _first_nonempty(r.get("item_no"), r.get("itemNo"), r.get("番号"), r.get("項番"))
    title = _first_nonempty(r.get("title"), r.get("name"), r.get("名称"), "")
    requirement_text = _first_nonempty(
        r.get("requirement_text"), r.get("requirementText"),
        r.get("text"), r.get("body"), r.get("definition"),
        title, item_no, "N/A",
    )
    usage_criteria_text = _first_nonempty(r.get("usage_criteria_text"), r.get("usageCriteriaText")) or None
    tech_criteria_text = _first_nonempty(r.get("tech_criteria_text"), r.get("techCriteriaText")) or None
    notes = _first_nonempty(r.get("notes"), r.get("notes_or_exclusions"), r.get("note")) or None
    version = _first_nonempty(r.get("version"), r.get("rev"), r.get("改訂")) or None
    effective_date = _to_date(r.get("effective_date") or r.get("effectiveDate"))

    if not item_no:
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


def _iter_rules_from_sheets(data: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """sheets 形式 {"sheets": {"シート名": {"export_items": [...]}}} を処理する。"""
    sheets = data.get("sheets", {})
    for sheet_name, sheet_data in sheets.items():
        if not isinstance(sheet_data, dict):
            continue
        if sheet_data.get("error"):
            continue  # header_not_found などエラーシートはスキップ

        for ex in (sheet_data.get("export_items") or []):
            if not isinstance(ex, dict):
                continue

            export_order_ref = ex.get("export_order_ref")
            export_ref_id = _get_ref_id(export_order_ref)
            export_ref_str = _get_ref_str(export_order_ref)
            export_order_item = _s(ex.get("export_order_item"))
            cargo_rules = ex.get("cargo_rules") or []

            if not cargo_rules:
                item_no = _first_nonempty(export_ref_id, export_ref_str,
                                          export_order_item[:120] if export_order_item else "")
                if item_no:
                    yield {
                        "regime": "JP_FX",
                        "list_name": sheet_name or None,
                        "item_no": item_no,
                        "title": export_order_item or item_no,
                        "requirement_text": export_order_item or item_no,
                        "usage_criteria_text": None,
                        "tech_criteria_text": None,
                        "notes": None,
                        "version": None,
                        "effective_date": None,
                    }
                continue

            for cr in cargo_rules:
                if not isinstance(cr, dict):
                    continue

                meti_order_ref = cr.get("meti_order_ref")
                meti_ref_id = _get_ref_id(meti_order_ref)
                meti_ref_str = _get_ref_str(meti_order_ref)
                meti_order_text = _s(cr.get("meti_order_text") or cr.get("definition"))
                term = _s(cr.get("term"))
                term_meaning = _s(cr.get("term_meaning"))
                notes_excl = _s(cr.get("notes_or_exclusions"))
                eccn = _s(cr.get("eccn"))
                substances = cr.get("substances") or []

                # item_no: ID を優先して "EL-3-1 / METI-2-1-1" 形式で構築
                parts: List[str] = []
                if export_ref_id:
                    parts.append(export_ref_id)
                if meti_ref_id:
                    parts.append(meti_ref_id)
                item_no = " / ".join(parts) if parts else _first_nonempty(
                    export_ref_str, meti_ref_str,
                    export_order_item[:80] if export_order_item else "")
                if not item_no:
                    continue

                title = _first_nonempty(export_order_item, term, export_ref_str, meti_ref_str, item_no)
                requirement_text = _first_nonempty(meti_order_text, export_order_item, term, title, "N/A")

                usage_criteria_text: Optional[str] = None
                if term or term_meaning:
                    usage_criteria_text = "\n".join([x for x in [term, term_meaning] if x]).strip() or None

                tech_lines: List[str] = []
                if eccn:
                    tech_lines.append(f"ECCN: {eccn}")
                if isinstance(substances, list) and substances:
                    sub_texts = [_get_substance_text(s) for s in substances if _get_substance_text(s)]
                    if sub_texts:
                        tech_lines.append("Substances:")
                        tech_lines.extend([f"- {st}" for st in sub_texts])
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


def _detect_and_iter_rules(data: Any) -> Iterable[Dict[str, Any]]:
    root = _normalize_root(data)
    # sheets 形式（実際の matrix.json）を最初に判定
    if isinstance(root, dict) and isinstance(root.get("sheets"), dict):
        yield from _iter_rules_from_sheets(root)
        return
    # FX-Matrix 形式（単一シート、export_items がルートにある）
    if isinstance(root, dict) and isinstance(root.get("export_items"), list):
        yield from _iter_rules_from_fx_matrix(data)
        return
    yield from _iter_rules_from_normalized(data)


def import_matrix_from_data(db: Session, data: Any, purge: bool = False) -> int:
    """
    JSON dict/list から MatrixRule を upsert する。
    purge=True の場合は先に全削除してから投入。
    登録/更新した件数を返す。
    """
    raw_rules = list(_detect_and_iter_rules(data))
    if not raw_rules:
        raise ValueError("JSONからルールが1件も取れませんでした。")

    if purge:
        db.query(MatrixRule).delete()
        db.commit()

    n = 0
    for raw in raw_rules:
        if "requirement_text" in raw and "item_no" in raw and "regime" in raw:
            rule = raw
        else:
            rule = _coerce_rule(raw)

        q = db.query(MatrixRule).filter(
            MatrixRule.regime == rule["regime"],
            MatrixRule.item_no == rule["item_no"],
        )
        if rule.get("version"):
            q = q.filter(MatrixRule.version == rule.get("version"))

        obj = q.first()
        if not obj:
            obj = MatrixRule(
                regime=rule["regime"],
                item_no=rule["item_no"],
                version=rule.get("version"),
            )

        obj.title = rule.get("title") or ""
        obj.requirement_text = rule.get("requirement_text") or "N/A"
        if hasattr(obj, "list_name"):
            obj.list_name = rule.get("list_name")
        if hasattr(obj, "usage_criteria_text"):
            obj.usage_criteria_text = rule.get("usage_criteria_text")
        if hasattr(obj, "tech_criteria_text"):
            obj.tech_criteria_text = rule.get("tech_criteria_text")
        if hasattr(obj, "notes"):
            obj.notes = rule.get("notes")
        if hasattr(obj, "effective_date"):
            obj.effective_date = rule.get("effective_date")
        if hasattr(obj, "updated_at"):
            obj.updated_at = datetime.utcnow()

        db.add(obj)
        n += 1

    db.commit()
    return n


# ---------------------------------------------------------------------------
# Patent インポート
# ---------------------------------------------------------------------------

def _to_ipc_raw(v: Any) -> Optional[str]:
    if not v:
        return None
    if isinstance(v, list):
        return ";".join([str(x).strip() for x in v if str(x).strip()])
    return str(v).strip()


def import_patents_from_data(db: Session, items: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Patent + PatentUsecase を upsert する（publication_number でキー）。
    統計 dict を返す。
    """
    inserted = 0
    updated = 0
    usecase_inserted = 0
    now = datetime.utcnow()

    for it in items:
        pub = (it.get("publication_number") or "").strip()
        if not pub:
            continue

        title = (it.get("title") or "").strip()
        assignee = (it.get("assignee") or it.get("applicant") or "").strip()
        abstract = (it.get("abstract") or "").strip()
        fulltext = (it.get("fulltext") or "").strip()
        # ipc_codes（list形式）または ipc_codes_raw（セミコロン区切り文字列）に対応
        ipc_raw = _to_ipc_raw(it.get("ipc_codes")) or _s(it.get("ipc_codes_raw")) or None

        # source_type / jplatpat_id（実特許識別）
        source_type = _s(it.get("source_type")) or "synthetic"
        jplatpat_id = it.get("jplatpat_id")  # int or None

        obj = db.query(Patent).filter(Patent.publication_number == pub).first()

        if obj:
            obj.title = title
            obj.assignee = assignee
            obj.abstract = abstract
            obj.fulltext = fulltext
            obj.ipc_codes_raw = ipc_raw
            # 実特許で上書き（合成→実特許への昇格を許可、逆は不可）
            if source_type != "synthetic" or obj.source_type == "synthetic":
                if hasattr(obj, "source_type"):
                    obj.source_type = source_type
            if jplatpat_id and hasattr(obj, "jplatpat_id"):
                obj.jplatpat_id = jplatpat_id
            if hasattr(obj, "updated_at"):
                obj.updated_at = now
            updated += 1
        else:
            kwargs: Dict[str, Any] = dict(
                publication_number=pub,
                title=title,
                assignee=assignee,
                abstract=abstract,
                fulltext=fulltext,
                ipc_codes_raw=ipc_raw,
                ingested_at=now,
                source_type=source_type,
                jplatpat_id=jplatpat_id,
            )
            if hasattr(Patent, "created_at"):
                kwargs["created_at"] = now
            if hasattr(Patent, "updated_at"):
                kwargs["updated_at"] = now
            obj = Patent(**kwargs)
            db.add(obj)
            db.flush()
            inserted += 1

        for uc in (it.get("usecases") or []):
            txt = (uc.get("text") or uc.get("usecase_text") or "").strip()
            if not txt:
                continue
            pu_kwargs: Dict[str, Any] = dict(
                patent_id=obj.id,
                usecase_text=txt,
                normalized_usecase_text=(uc.get("normalized") or None),
                extraction_method=uc.get("method") or "json",
                quality_score=uc.get("quality_score"),
            )
            if hasattr(PatentUsecase, "created_at"):
                pu_kwargs["created_at"] = now
            if hasattr(PatentUsecase, "updated_at"):
                pu_kwargs["updated_at"] = now
            db.add(PatentUsecase(**pu_kwargs))
            usecase_inserted += 1

    db.commit()
    return {
        "patents_inserted": inserted,
        "patents_updated": updated,
        "usecases_inserted": usecase_inserted,
        "total_in_json": len(items),
    }
