from __future__ import annotations

from sqlalchemy.orm import Session
from .models import DapApp, DapPage, DapTarget, DapRule, DapIntervention


def build_runtime_config(db: Session, app_key: str) -> dict:
    app = db.query(DapApp).filter(DapApp.app_key == app_key).one()

    pages = db.query(DapPage).filter(DapPage.app_id == app.id).all()
    rules = db.query(DapRule).filter(DapRule.app_id == app.id).all()
    page_by_id = {p.id: p for p in pages}

    targets = (
        db.query(DapTarget)
        .join(DapPage, DapTarget.page_id == DapPage.id)
        .filter(DapPage.app_id == app.id)
        .all()
    )

    interventions = (
        db.query(DapIntervention)
        .join(DapPage, DapIntervention.page_id == DapPage.id)
        .filter(DapPage.app_id == app.id)
        .all()
    )

    return {
        "app": {"app_key": app.app_key, "name": app.name},
        "pages": [
            {
                "page_id": p.page_key,
                "name": p.name,
                "url_regex": p.url_regex,
                "must_have": p.must_have or {},
            }
            for p in pages
        ],
        "targets": [
            {
                "target_id": t.target_key,
                "page_id": page_by_id[t.page_id].page_key,
                "description": t.description,
                "anchors": t.anchors or [],
            }
            for t in targets
        ],
        "rules": [
            {
                "rule_id": r.rule_key,
                "name": r.name,
                "type": r.rule_type,
                "params": r.params or {},
                "severity": r.severity,
            }
            for r in rules
        ],
        "interventions": [
            {
                "id": iv.intervention_key,
                "name": iv.name,
                "page_id": page_by_id[iv.page_id].page_key,
                "trigger": iv.trigger or {},
                "rule_id": iv.rule_key,
                "block_action": bool(iv.block_action),
                "coach": iv.coach or {},
                "actions": iv.actions or [],
            }
            for iv in interventions
        ],
    }
