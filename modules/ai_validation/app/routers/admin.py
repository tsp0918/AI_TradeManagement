# app/routers/admin.py
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.services.data_import import import_matrix_from_data, import_patents_from_data

router = APIRouter(tags=["admin"])


@router.get("/admin/faiss/status")
def faiss_status(request: Request):
    """FAISS インデックスの現在状態を返す。"""
    from platform_core.services.faiss_e5_service import (
        is_ready, ntotal_layer_a, ntotal_layer_b, ntotal_layer_c
    )
    return {
        "faiss_ready": getattr(request.app.state, "faiss_ready", False),
        "is_ready_fn":  is_ready(),
        "layer_a_ntotal": ntotal_layer_a(),
        "layer_b_ntotal": ntotal_layer_b(),
        "layer_c_ntotal": ntotal_layer_c(),
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
    return templates.TemplateResponse("admin_import.html", {"request": request, **ctx})


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
