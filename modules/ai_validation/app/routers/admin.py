# app/routers/admin.py
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.services.data_import import import_matrix_from_data, import_patents_from_data


# ── system_settings helpers ──────────────────────────────────────────────────

def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.execute(text("SELECT value FROM system_settings WHERE key=:k"), {"k": key}).fetchone()
    return row[0] if row else default


def set_setting(db: Session, key: str, value: str) -> None:
    db.execute(
        text("INSERT INTO system_settings(key, value, updated_at) VALUES(:k,:v,datetime('now')) "
             "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at"),
        {"k": key, "v": value},
    )
    db.commit()

router = APIRouter(tags=["admin"])


@router.get("/admin/faiss/status")
def faiss_status(request: Request):
    """FAISS インデックスの現在状態を返す。"""
    from platform_core.services.faiss_e5_service import (
        is_ready, layer_c_available, ntotal_layer_a, ntotal_layer_b, ntotal_layer_c
    )
    return {
        "faiss_ready": getattr(request.app.state, "faiss_ready", False),
        "is_ready_fn":  is_ready(),
        "layer_a_ntotal": ntotal_layer_a(),
        "layer_b_ntotal": ntotal_layer_b(),
        "layer_c_available": layer_c_available(),
        "layer_c_ntotal": ntotal_layer_c() if layer_c_available() else None,
    }


@router.post("/admin/faiss/reload")
async def faiss_reload(request: Request):
    """FAISS インデックスを再ロードする（デバッグ / 起動失敗時の回復用）。"""
    import asyncio
    from platform_core.services.faiss_e5_service import preload
    request.app.state.faiss_ready = False
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, preload)
        request.app.state.faiss_ready = True
        from platform_core.services.faiss_e5_service import ntotal_layer_a, ntotal_layer_b, ntotal_layer_c
        return {
            "ok": True,
            "layer_a": ntotal_layer_a(),
            "layer_b": ntotal_layer_b(),
            "layer_c": ntotal_layer_c(),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _render(request: Request, **ctx):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "admin_import.html", {**ctx})


@router.get("/ui/admin/import", response_class=HTMLResponse)
def admin_import_form(request: Request):
    return _render(request)


@router.post("/ui/admin/import/matrix", response_class=HTMLResponse)
async def admin_import_matrix(
    request: Request,
    file: UploadFile = File(...),
    purge: bool = Form(False),
    db: Session = Depends(get_db),
):
    if not file.filename:
        return _render(request, matrix_error="ファイルが選択されていません。")
    try:
        content = await file.read()
        data = json.loads(content)
        count = import_matrix_from_data(db, data, purge=purge)
        return _render(request, matrix_message=f"MatrixRule を {count} 件インポートしました。")
    except json.JSONDecodeError as e:
        return _render(request, matrix_error=f"JSONパースエラー: {e}")
    except Exception as e:
        return _render(request, matrix_error=str(e))


@router.post("/ui/admin/import/patents", response_class=HTMLResponse)
async def admin_import_patents(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not file.filename:
        return _render(request, patents_error="ファイルが選択されていません。")
    try:
        content = await file.read()
        doc = json.loads(content)
        if isinstance(doc, list):
            items = doc
        elif isinstance(doc, dict) and isinstance(doc.get("items"), list):
            items = doc["items"]
        else:
            return _render(request, patents_error="patents.json は list または { items: [...] } 形式にしてください。")
        result = import_patents_from_data(db, items)
        msg = (
            f"インポート完了: 新規 {result['patents_inserted']} 件 / "
            f"更新 {result['patents_updated']} 件 / "
            f"用途データ {result['usecases_inserted']} 件 "
            f"（JSON合計 {result['total_in_json']} 件）"
        )
        return _render(request, patents_message=msg)
    except json.JSONDecodeError as e:
        return _render(request, patents_error=f"JSONパースエラー: {e}")
    except Exception as e:
        return _render(request, patents_error=str(e))


# ── 閾値・パラメーター設定 UI ─────────────────────────────────────────────────

_SETTINGS_DEFS = [
    {
        "key":   "matrix_match_threshold",
        "label": "２リスト マッチ閾値",
        "type":  "float",
        "min":   "0.50",
        "max":   "0.99",
        "step":  "0.01",
        "default": "0.82",
        "desc":  "Layer A ベクトル類似度の下限。0.82 で適合率95%・再現率95%。下げると判定ヒット増、上げると精度向上。",
    },
    {
        "key":   "matrix_match_top_k",
        "label": "マッチ上位件数 (top_k)",
        "type":  "int",
        "min":   "1",
        "max":   "30",
        "step":  "1",
        "default": "10",
        "desc":  "1使用目的あたりに返すマッチ候補の最大数。増やすと網羅性向上、処理時間も増加。",
    },
]


def _render_settings(request: Request, db: Session, **ctx):
    templates = request.app.state.templates
    settings = {d["key"]: get_setting(db, d["key"], d["default"]) for d in _SETTINGS_DEFS}
    return templates.TemplateResponse(
        request, "admin_settings.html",
        {"settings": settings, "defs": _SETTINGS_DEFS, **ctx},
    )


@router.get("/ui/admin/settings", response_class=HTMLResponse)
def admin_settings_form(request: Request, db: Session = Depends(get_db)):
    return _render_settings(request, db)


@router.post("/ui/admin/settings", response_class=HTMLResponse)
async def admin_settings_save(
    request: Request,
    db: Session = Depends(get_db),
):
    form = await request.form()
    errors = []
    for d in _SETTINGS_DEFS:
        val = form.get(d["key"], "").strip()
        if not val:
            continue
        try:
            if d["type"] == "float":
                v = float(val)
                assert float(d["min"]) <= v <= float(d["max"])
            else:
                v = int(val)
                assert int(d["min"]) <= v <= int(d["max"])
            set_setting(db, d["key"], str(v))
        except Exception:
            errors.append(f"{d['label']}: 値が不正です（{val}）")
    if errors:
        return _render_settings(request, db, error=" / ".join(errors))
    return _render_settings(request, db, message="設定を保存しました。")
