from __future__ import annotations

import os

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from platform_core.module_sdk import ModuleInfo, build_lifespan, health_router

from .db import Base, engine, get_db
from .models import DapApp, DapPage, DapTarget, DapRule, DapIntervention, DapRelease, DapEvent
from .schemas import (
    AppCreate, PageCreate, TargetUpsert, RuleCreate, InterventionCreate,
    ReleaseCreate, PublishIn, PublishAutoIn, InterventionUpdate, PageUpdate, EventIn
)
from .runtime_builder import build_runtime_config
from .utils import stable_etag
from .seed import seed_if_empty

MODULE = ModuleInfo(
    key="dap",
    name="DAP / AIオーケストレーター",
    base_url=os.environ.get("MODULE_DAP_URL", "http://localhost:8010"),
    description="クロスモジュール AI エージェント・各画面コーチング・入力補助",
    capabilities=["orchestrate", "coach", "cross_module_workflow"],
    data_contracts={
        "input":  ["AiRun", "Assessment", "Company"],
        "output": ["WorkflowResult", "CoachingEvent"],
    },
)

Base.metadata.create_all(bind=engine)


async def _startup() -> None:
    db = next(get_db())
    try:
        seed_if_empty(db)
    finally:
        db.close()


app = FastAPI(
    title="DAP Coach Server (Admin + Runtime)",
    lifespan=build_lifespan(MODULE, on_startup=_startup),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

app.include_router(health_router)

# ─────────────────── Admin UI ────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def admin_home(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})

# ─────────────────── Runtime Config ──────────────────────────────────────────

@app.get("/runtime/config")
def runtime_config(app_key: str, env: str = "local", db: Session = Depends(get_db)):
    dap_app = db.query(DapApp).filter(DapApp.app_key == app_key).one_or_none()
    if not dap_app:
        raise HTTPException(404, "app not found")
    rel = (
        db.query(DapRelease)
        .filter(DapRelease.app_id == dap_app.id, DapRelease.env == env, DapRelease.status == "published")
        .order_by(DapRelease.created_at.desc())
        .first()
    )
    if not rel or not rel.runtime_config:
        cfg = build_runtime_config(db, app_key)
        return JSONResponse({"env": env, "version": "live", "config": cfg, "etag": stable_etag(cfg)})
    return JSONResponse({"env": env, "version": rel.version, "config": rel.runtime_config, "etag": stable_etag(rel.runtime_config)})

# ─────────────────── Admin API (CRUD) ────────────────────────────────────────

@app.get("/api/apps")
def list_apps(db: Session = Depends(get_db)):
    apps = db.query(DapApp).order_by(DapApp.created_at.desc()).all()
    return [{"id": a.id, "app_key": a.app_key, "name": a.name} for a in apps]

@app.post("/api/apps")
def create_app_endpoint(payload: AppCreate, db: Session = Depends(get_db)):
    a = DapApp(app_key=payload.app_key, name=payload.name)
    db.add(a); db.commit(); db.refresh(a)
    return {"id": a.id, "app_key": a.app_key, "name": a.name}

@app.get("/api/pages")
def list_pages(app_id: int, db: Session = Depends(get_db)):
    pages = db.query(DapPage).filter(DapPage.app_id == app_id).order_by(DapPage.created_at.desc()).all()
    return [{"id": p.id, "app_id": p.app_id, "page_key": p.page_key, "name": p.name, "url_regex": p.url_regex, "must_have": p.must_have or {}} for p in pages]

@app.post("/api/pages")
def create_page(payload: PageCreate, db: Session = Depends(get_db)):
    p = DapPage(app_id=payload.app_id, page_key=payload.page_key, name=payload.name, url_regex=payload.url_regex, must_have=payload.must_have)
    db.add(p); db.commit(); db.refresh(p)
    return {"id": p.id}

@app.get("/api/targets")
def list_targets(page_id: int, db: Session = Depends(get_db)):
    t = db.query(DapTarget).filter(DapTarget.page_id == page_id).order_by(DapTarget.created_at.desc()).all()
    return [{"id": x.id, "page_id": x.page_id, "target_key": x.target_key, "description": x.description, "anchors": x.anchors} for x in t]

@app.post("/api/targets/upsert")
def upsert_target(payload: TargetUpsert, db: Session = Depends(get_db)):
    existing = db.query(DapTarget).filter(DapTarget.page_id == payload.page_id, DapTarget.target_key == payload.target_key).one_or_none()
    if existing:
        existing.description = payload.description; existing.anchors = payload.anchors
        db.add(existing); db.commit()
        return {"id": existing.id, "updated": True}
    t = DapTarget(page_id=payload.page_id, target_key=payload.target_key, description=payload.description, anchors=payload.anchors)
    db.add(t); db.commit(); db.refresh(t)
    return {"id": t.id, "updated": False}

@app.get("/api/rules")
def list_rules(app_id: int, db: Session = Depends(get_db)):
    r = db.query(DapRule).filter(DapRule.app_id == app_id).order_by(DapRule.created_at.desc()).all()
    return [{"id": x.id, "rule_key": x.rule_key, "name": x.name, "rule_type": x.rule_type, "params": x.params, "severity": x.severity} for x in r]

@app.post("/api/rules")
def create_rule(payload: RuleCreate, db: Session = Depends(get_db)):
    r = DapRule(app_id=payload.app_id, rule_key=payload.rule_key, name=payload.name, rule_type=payload.rule_type, params=payload.params, severity=payload.severity)
    db.add(r); db.commit(); db.refresh(r)
    return {"id": r.id}

@app.get("/api/interventions")
def list_interventions(page_id: int, db: Session = Depends(get_db)):
    ivs = db.query(DapIntervention).filter(DapIntervention.page_id == page_id).order_by(DapIntervention.created_at.desc()).all()
    return [{"id": iv.id, "intervention_key": iv.intervention_key, "name": iv.name, "trigger": iv.trigger, "rule_key": iv.rule_key, "block_action": bool(iv.block_action), "coach": iv.coach, "actions": iv.actions} for iv in ivs]

@app.post("/api/interventions")
def create_intervention(payload: InterventionCreate, db: Session = Depends(get_db)):
    iv = DapIntervention(page_id=payload.page_id, intervention_key=payload.intervention_key, name=payload.name, trigger=payload.trigger, rule_key=payload.rule_key, block_action=payload.block_action, coach=payload.coach, actions=payload.actions)
    db.add(iv); db.commit(); db.refresh(iv)
    return {"id": iv.id}

@app.put("/api/interventions/{iv_id}")
def update_intervention(iv_id: int, payload: InterventionUpdate, db: Session = Depends(get_db)):
    iv = db.query(DapIntervention).filter(DapIntervention.id == iv_id).one_or_none()
    if not iv: raise HTTPException(404, "intervention not found")
    iv.name = payload.name; iv.trigger = payload.trigger; iv.rule_key = payload.rule_key
    iv.block_action = payload.block_action; iv.coach = payload.coach; iv.actions = payload.actions
    db.add(iv); db.commit(); db.refresh(iv)
    return {"ok": True, "id": iv.id}

@app.delete("/api/interventions/{iv_id}")
def delete_intervention(iv_id: int, db: Session = Depends(get_db)):
    iv = db.query(DapIntervention).filter(DapIntervention.id == iv_id).one_or_none()
    if not iv: raise HTTPException(404, "intervention not found")
    db.delete(iv); db.commit()
    return {"ok": True}

@app.delete("/api/targets/{target_id}")
def delete_target(target_id: int, db: Session = Depends(get_db)):
    t = db.query(DapTarget).filter(DapTarget.id == target_id).one_or_none()
    if not t: raise HTTPException(404, "target not found")
    db.delete(t); db.commit()
    return {"ok": True}

@app.put("/api/pages/{page_id}")
def update_page(page_id: int, payload: PageUpdate, db: Session = Depends(get_db)):
    p = db.query(DapPage).filter(DapPage.id == page_id).one_or_none()
    if not p: raise HTTPException(404, "page not found")
    p.name = payload.name; p.url_regex = payload.url_regex; p.must_have = payload.must_have
    db.add(p); db.commit()
    return {"ok": True}

@app.delete("/api/pages/{page_id}")
def delete_page(page_id: int, db: Session = Depends(get_db)):
    p = db.query(DapPage).filter(DapPage.id == page_id).one_or_none()
    if not p: raise HTTPException(404, "page not found")
    db.delete(p); db.commit()
    return {"ok": True}

@app.get("/api/releases/latest")
def get_latest_release(app_id: int, env: str = "local", db: Session = Depends(get_db)):
    rel = db.query(DapRelease).filter(DapRelease.app_id == app_id, DapRelease.env == env, DapRelease.status == "published").order_by(DapRelease.created_at.desc()).first()
    return {"version": None} if not rel else {"version": rel.version, "id": rel.id}

@app.post("/api/releases")
def create_release(payload: ReleaseCreate, db: Session = Depends(get_db)):
    rel = DapRelease(app_id=payload.app_id, env=payload.env, version=payload.version, status="draft", runtime_config=None)
    db.add(rel); db.commit(); db.refresh(rel)
    return {"id": rel.id}

@app.post("/api/publish")
def publish(payload: PublishAutoIn, db: Session = Depends(get_db)):
    dap_app = db.query(DapApp).filter(DapApp.id == payload.app_id).one_or_none()
    if not dap_app: raise HTTPException(404, "app not found")
    latest = db.query(DapRelease).filter(DapRelease.app_id == payload.app_id, DapRelease.env == payload.env, DapRelease.status == "published").order_by(DapRelease.created_at.desc()).first()
    try:
        parts = latest.version.lstrip("v").split(".")
        new_version = f"{parts[0]}.{int(parts[1]) + 1}.{parts[2]}"
    except Exception:
        new_version = "0.1.0"
    cfg = build_runtime_config(db, dap_app.app_key)
    rel = DapRelease(app_id=payload.app_id, env=payload.env, version=new_version, status="published", runtime_config=cfg)
    db.add(rel); db.commit()
    return {"ok": True, "env": payload.env, "version": new_version}

# ─────────────────── Recorder & Events ───────────────────────────────────────

@app.post("/api/recorder/target")
def recorder_target(payload: dict, db: Session = Depends(get_db)):
    app_obj = db.query(DapApp).filter(DapApp.app_key == payload.get("app_key")).one_or_none()
    if not app_obj: raise HTTPException(404, "app not found")
    page = db.query(DapPage).filter(DapPage.app_id == app_obj.id, DapPage.page_key == payload.get("page_key")).one_or_none()
    if not page: raise HTTPException(404, "page not found")
    target_key = payload.get("target_key"); anchors = payload.get("anchors") or []; description = payload.get("description") or ""
    existing = db.query(DapTarget).filter(DapTarget.page_id == page.id, DapTarget.target_key == target_key).one_or_none()
    if existing:
        existing.anchors = anchors; existing.description = description; db.add(existing); db.commit()
        return {"ok": True, "updated": True, "id": existing.id}
    t = DapTarget(page_id=page.id, target_key=target_key, description=description, anchors=anchors)
    db.add(t); db.commit(); db.refresh(t)
    return {"ok": True, "updated": False, "id": t.id}

@app.post("/api/events")
def post_event(payload: EventIn, db: Session = Depends(get_db)):
    e = DapEvent(ts=payload.ts, app_key=payload.app_key, page_id=payload.page_id, user_pseudo_id=payload.user_pseudo_id, event_type=payload.event_type, event_name=payload.event_name, context=payload.context)
    db.add(e); db.commit()
    return {"ok": True}

@app.get("/api/analytics/summary")
def analytics_summary(app_key: str, db: Session = Depends(get_db)):
    rows = db.query(DapEvent).filter(DapEvent.app_key == app_key).order_by(DapEvent.created_at.desc()).limit(200).all()
    return [{"ts": r.ts, "page_id": r.page_id, "event_type": r.event_type, "event_name": r.event_name, "context": r.context} for r in rows]
