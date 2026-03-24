# app/routers/sds.py
from __future__ import annotations

import os
import json

from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, Form, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from PyPDF2 import PdfReader

from ..database import get_db
from ..models import Product
from ..services.ollama_client import OllamaClient
from ..services.sds_analyzer import analyze_sds_to_mapping

router = APIRouter(prefix="/products", tags=["sds"])
templates = Jinja2Templates(directory="templates")

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _safe_resolve_upload_path(filename: str) -> str:
    """ファイル名を検証して uploads/ 配下の絶対パスを返す。"""
    if not filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="不正なファイル名です。")
    base = os.path.abspath(UPLOAD_DIR)
    path = os.path.abspath(os.path.join(UPLOAD_DIR, filename))
    if not path.startswith(base + os.sep):
        raise HTTPException(status_code=400, detail="uploads配下以外は参照できません。")
    return path


def _list_existing_pdfs() -> list[str]:
    try:
        pdfs = [fn for fn in os.listdir(UPLOAD_DIR) if fn.lower().endswith(".pdf")]
        return sorted(pdfs)
    except Exception:
        return []


def apply_ai_regulation_mapping(product: Product, mapping: dict) -> None:
    product.ghs_signal_word = mapping.get("ghs_signal_word") or product.ghs_signal_word

    pictos = mapping.get("ghs_pictograms")
    if isinstance(pictos, list):
        product.ghs_pictograms = ",".join(pictos)
    elif isinstance(pictos, str):
        product.ghs_pictograms = pictos

    def as_bool(v):
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ["true", "1", "yes", "y"]
        return None

    for field in [
        "is_poison", "is_deleterious", "is_kashinho", "is_kashinho_class_I", "is_kashinho_class_II",
        "is_roudou_anzen_eisei", "is_prtr", "is_shoubouho", "is_high_pressure_gas",
    ]:
        if field in mapping:
            v = as_bool(mapping[field])
            if v is not None:
                setattr(product, field, v)

    if mapping.get("ghs_h_statements") is not None:
        stmts = mapping["ghs_h_statements"]
        product.ghs_h_statements = "\n".join(stmts) if isinstance(stmts, list) else str(stmts)
    if mapping.get("ghs_p_statements") is not None:
        stmts = mapping["ghs_p_statements"]
        product.ghs_p_statements = "\n".join(stmts) if isinstance(stmts, list) else str(stmts)
    if mapping.get("ghs_classes") is not None:
        classes = mapping["ghs_classes"]
        product.ghs_classes = ", ".join(classes) if isinstance(classes, list) else str(classes)


@router.get("/{product_id}/sds", response_class=HTMLResponse)
def sds_upload_form(
    product_id: int,
    request: Request,
    success: int = 0,
    db: Session = Depends(get_db),
):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    message = "SDS解析が完了しました。規制情報が更新されました。" if success else None

    return templates.TemplateResponse(
        request, "product_sds_upload.html",
        {
            "product": product,
            "existing_pdfs": _list_existing_pdfs(),
            "message": message,
        },
    )


@router.post("/{product_id}/sds", response_class=HTMLResponse)
async def sds_upload(
    product_id: int,
    request: Request,
    file: UploadFile | None = File(None),
    existing_filename: str = Form(""),
    db: Session = Depends(get_db),
):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    def _render(error: str | None = None, message: str | None = None):
        return templates.TemplateResponse(
            request, "product_sds_upload.html",
            {
                "product": product,
                "existing_pdfs": _list_existing_pdfs(),
                "error": error,
                "message": message,
            },
        )

    # 1. ファイル決定
    if file is not None and file.filename:
        safe_fn = os.path.basename(file.filename)
        if not safe_fn:
            return _render(error="ファイル名が不正です。")
        dest_name = f"product_{product_id}_{safe_fn}"
        try:
            filepath = _safe_resolve_upload_path(dest_name)
        except HTTPException as e:
            return _render(error=e.detail)
        with open(filepath, "wb") as fp:
            fp.write(await file.read())
        product.sds_file_path = filepath

    elif existing_filename.strip():
        try:
            filepath = _safe_resolve_upload_path(existing_filename.strip())
        except HTTPException as e:
            return _render(error=e.detail)
        if not os.path.exists(filepath):
            return _render(error="選択したPDFが uploads に存在しません。")
        product.sds_file_path = filepath

    else:
        return _render(error="PDFをアップロードするか、既存PDFを選択してください。")

    # 2. テキスト抽出
    try:
        reader = PdfReader(product.sds_file_path)
        sds_text = "\n".join([(p.extract_text() or "") for p in reader.pages])
    except Exception as e:
        product.regulation_ai_raw = f"SDS text extract error: {repr(e)}"
        db.commit()
        return _render(error=f"PDF読み込みエラー: {repr(e)}")

    if not (sds_text or "").strip():
        msg = (
            "SDSテキストが抽出できませんでした。"
            "（画像PDF/スキャンPDFの可能性。OCRかテキスト版SDSが必要です）"
        )
        product.regulation_ai_raw = msg
        db.commit()
        return _render(error=msg)

    # 3. AI解析（ルール優先、必要時 Ollama）
    try:
        ollama = OllamaClient()
        result = await analyze_sds_to_mapping(sds_text, ollama)
    except Exception as e:
        product.regulation_ai_raw = f"AI解析エラー: {repr(e)}"
        db.commit()
        return _render(error=f"AI解析に失敗しました: {repr(e)}")

    mapping = result.get("mapping") or {}
    raw = result.get("raw") or ""
    product.regulation_ai_raw = raw if raw else json.dumps(mapping, ensure_ascii=False)

    if mapping:
        apply_ai_regulation_mapping(product, mapping)

    db.commit()
    return RedirectResponse(
        url=f"/products/{product_id}/sds?success=1",
        status_code=303,
    )
