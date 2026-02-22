from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.db.models.patent import Patent, PatentUsecase


# ------------------------
# util
# ------------------------
def project_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, ".."))


def read_json(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)

    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict) and isinstance(doc.get("items"), list):
        return doc["items"]

    raise ValueError("patents.json must be list or { items: [...] }")


def to_ipc_raw(v: Any) -> Optional[str]:
    if not v:
        return None
    if isinstance(v, list):
        return ";".join([str(x).strip() for x in v if str(x).strip()])
    return str(v).strip()


# ------------------------
# upsert logic
# ------------------------
def upsert_patents(db: Session, items: List[Dict[str, Any]]) -> Dict[str, int]:
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
        ipc_raw = to_ipc_raw(it.get("ipc_codes"))

        obj = db.query(Patent).filter(Patent.publication_number == pub).first()

        if obj:
            obj.title = title
            obj.assignee = assignee
            obj.abstract = abstract
            obj.fulltext = fulltext
            obj.ipc_codes_raw = ipc_raw
            obj.updated_at = now
            updated += 1
        else:
            obj = Patent(
                publication_number=pub,
                title=title,
                assignee=assignee,
                abstract=abstract,
                fulltext=fulltext,
                ipc_codes_raw=ipc_raw,
                ingested_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(obj)
            db.flush()  # ← id を即時確定
            inserted += 1

        # ---- usecases（evidence 用） ----
        usecases = it.get("usecases") or []
        for uc in usecases:
            txt = (uc.get("text") or "").strip()
            if not txt:
                continue

            pu = PatentUsecase(
                patent_id=obj.id,
                usecase_text=txt,
                normalized_usecase_text=(uc.get("normalized") or None),
                extraction_method=uc.get("method") or "json",
                quality_score=uc.get("quality_score"),
                created_at=now,
                updated_at=now,
            )
            db.add(pu)
            usecase_inserted += 1

    return {
        "patents_inserted": inserted,
        "patents_updated": updated,
        "usecases_inserted": usecase_inserted,
        "total_in_json": len(items),
    }


# ------------------------
# entrypoint
# ------------------------
def main():
    json_path = os.path.join(project_root(), "data", "patents.json")
    items = read_json(json_path)

    db = SessionLocal()
    try:
        res = upsert_patents(db, items)
        db.commit()
        print("[OK] patents.json imported")
        print(res)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
