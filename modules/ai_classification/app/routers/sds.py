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
    if not filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="不正なファイル名です。")
    base = os.path.abspath(UPLOAD_DIR)
    path = os.path.abspath(os.path.join(UPLOAD_DIR, filename))
    if not path.startswith(base + os.sep):
        raise HTTPException(status_code=400, detail="uploads配下以外は参照できません。")
    return path


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
        product.ghs_h_statements = "\n".join(mapping["ghs_h_statements"]) if isinstance(mapping["ghs_h_statements"], list) else str(mapping["ghs_h_statements"])
    if mapping.get("ghs_p_statements") is not None:
        product.ghs_p_statements = "\n".join(mapping["ghs_p_statements"]) if isinstance(mapping["ghs_p_statements"], list) else str(mapping["ghs_p_statements"])
    if mapping.get("ghs_classes") is not None:
        product.ghs_classes = ", ".join(mapping["ghs_classes"]) if isinstance(mapping["ghs_classes"], list) else str(mapping["ghs_classes"])


@router.get("/{product_id}/sds", response_class=HTMLResponse)
def sds_upload_form(product_id: int, request: Request, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    existing_pdfs = []
    try:
        for fn in os.listdir(UPLOAD_DIR):
            if fn.lower().endswith(".pdf"):
                existing_pdfs.append(fn)
        existing_pdfs.sort()
    except Exception:
        existing_pdfs = []

    return templates.TemplateResponse(
        "product_sds_upload.html",
        {"request": request, "product": product, "existing_pdfs": existing_pdfs},
    )


@router.post("/{product_id}/sds")
async def sds_upload(
    product_id: int,
    file: UploadFile | None = File(None),
    existing_filename: str = Form(""),
    db: Session = Depends(get_db),
):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # 1) ファイル決定
    if file is not None:
        filename = f"product_{product_id}_{file.filename}"
        filepath = os.path.join(UPLOAD_DIR, filename)
        with open(filepath, "wb") as f:
            f.write(await file.read())
        product.sds_file_path = filepath

    elif existing_filename.strip():
        filepath = _safe_resolve_upload_path(existing_filename.strip())
        if not os.path.exists(filepath):
            raise HTTPException(status_code=400, detail="選択したPDFが uploads に存在しません。")
        product.sds_file_path = filepath

    else:
        raise HTTPException(status_code=400, detail="PDFをアップロードするか、既存PDFを選択してください。")

    # 2) テキスト抽出
    try:
        reader = PdfReader(product.sds_file_path)
        sds_text = "\n".join([(p.extract_text() or "") for p in reader.pages])
    except Exception as e:
        product.regulation_ai_raw = f"SDS text extract error: {repr(e)}"
        db.commit()
        return RedirectResponse(url=f"/products/{product_id}/edit", status_code=303)

    if not (sds_text or "").strip():
        product.regulation_ai_raw = (
            "SDSテキストが抽出できませんでした。"
            "（画像PDF/スキャンPDFの可能性。OCRかテキスト版SDSが必要です）"
        )
        db.commit()
        return RedirectResponse(url=f"/products/{product_id}/edit", status_code=303)

    # 3) 解析（rule優先、必要なら短文でOllama）
    ollama = OllamaClient()
    result = await analyze_sds_to_mapping(sds_text, ollama)

    mapping = result.get("mapping") or {}
    raw = result.get("raw") or ""

    # raw（診断情報）を必ず残す
    product.regulation_ai_raw = raw if raw else json.dumps(mapping, ensure_ascii=False)

    if mapping:
        apply_ai_regulation_mapping(product, mapping)

    db.commit()
    return RedirectResponse(url=f"/products/{product_id}/edit", status_code=303)
