# app/routers/products.py
from __future__ import annotations

import io
import json
from datetime import datetime

import pandas as pd
from fastapi import (
    APIRouter,
    Request,
    Depends,
    Form,
    UploadFile,
    File,
    HTTPException,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

import httpx

from ..database import get_db
from ..models import Product, BomHistory
from ..settings import settings
from ..services.external_app_client import ExternalAppClient

from urllib.parse import quote_plus as _quote_plus

router = APIRouter(tags=["products"])
templates = Jinja2Templates(directory="templates")
templates.env.filters["urlencode"] = lambda s: _quote_plus(str(s) if s is not None else "")


# =========================
# 該非判定ロジック（既存移植）
# =========================
def evaluate_export_control(product: Product, db: Session):
    reasons = []
    severity = 0  # 0: 非該当寄り、1: 要確認、2: 要注意、3: 該当

    if product.eccn:
        eccn = product.eccn.strip().upper()
        if eccn and eccn != "EAR99":
            severity = max(severity, 3)
            reasons.append(f"ECCN指定あり: {eccn}")

    if product.is_poison:
        severity = max(severity, 3)
        reasons.append("毒物（毒物及び劇物取締法）")

    if product.is_deleterious:
        severity = max(severity, 3)
        reasons.append("劇物（毒物及び劇物取締法）")

    if product.is_kashinho:
        severity = max(severity, 3)
        reasons.append("火薬類取締法対象（総合）")

    if getattr(product, "is_kashinho_class_I", False):
        severity = max(severity, 3)
        reasons.append("火薬類取締法 第1種")

    if getattr(product, "is_kashinho_class_II", False):
        severity = max(severity, 3)
        reasons.append("火薬類取締法 第2種")

    if product.is_roudou_anzen_eisei:
        severity = max(severity, 2)
        reasons.append("労働安全衛生法に基づく有害物（要注意）")

    if product.is_prtr:
        severity = max(severity, 2)
        reasons.append("PRTR対象物質を含む可能性（要注意）")

    if product.is_shoubouho:
        severity = max(severity, 2)
        reasons.append("消防法危険物に該当")

    if product.is_high_pressure_gas:
        severity = max(severity, 2)
        reasons.append("高圧ガス保安法対象（要注意）")

    if product.ghs_signal_word:
        sw = product.ghs_signal_word.strip()
        if "危険" in sw:
            severity = max(severity, 2)
            reasons.append(f"GHSシグナルワード: {sw}")

    # --- BOM からの波及判定 ---
    bom_flagged = []
    if product.bom_json:
        try:
            bom_items = json.loads(product.bom_json)
        except Exception:
            bom_items = []

        for item in bom_items:
            if item.get("kind") != "material":
                continue

            comp_id = item.get("component_id")
            if not comp_id:
                continue

            comp = db.get(Product, comp_id)
            if not comp:
                continue

            comp_reasons = []
            comp_severity = 0

            if comp.eccn and comp.eccn.strip().upper() != "EAR99":
                comp_severity = max(comp_severity, 3)
                comp_reasons.append(f"ECCN={comp.eccn}")

            if comp.is_poison:
                comp_severity = max(comp_severity, 3)
                comp_reasons.append("毒物")
            if comp.is_deleterious:
                comp_severity = max(comp_severity, 3)
                comp_reasons.append("劇物")

            if comp.is_kashinho or getattr(comp, "is_kashinho_class_I", False) or getattr(
                comp, "is_kashinho_class_II", False
            ):
                comp_severity = max(comp_severity, 3)
                comp_reasons.append("火薬類取締法")

            if comp.is_prtr:
                comp_severity = max(comp_severity, 2)
                comp_reasons.append("PRTR")
            if comp.is_shoubouho:
                comp_severity = max(comp_severity, 2)
                comp_reasons.append("消防法")
            if comp.is_high_pressure_gas:
                comp_severity = max(comp_severity, 2)
                comp_reasons.append("高圧ガス")

            if comp_severity >= 2:
                bom_flagged.append(f"{comp.code} ({comp.name}) : {', '.join(comp_reasons)}")
                severity = max(severity, comp_severity - 1)

    if bom_flagged:
        reasons.append("BOM構成品目に規制対象の疑い: " + " / ".join(bom_flagged))

    if severity >= 3:
        status = "controlled"
        label = "該当（要専門家レビュー）"
    elif severity == 2:
        status = "needs_attention"
        label = "要注意（該非要確認）"
    elif severity == 1:
        status = "to_be_checked"
        label = "要確認（情報不足）"
    else:
        status = "non_controlled"
        label = "非該当と推定（要最終確認）"

    if not reasons:
        reasons.append("明示的な規制フラグは検出されませんでした。")

    reason_text = f"[{label}] " + " / ".join(reasons)

    return {"status": status, "label": label, "reason": reason_text}


# =========================
# 外部AI payload 要約（UI表示用）
# =========================
def build_external_ai_summary(product: Product):
    """
    product.external_eval_payload に保存されている JSON 文字列を解析し、
    UI向けの「根拠 + 次アクション」を抽出する。

    - {"request_id":.., "payload": {...}} 形式
    - payload直の形式
    どちらでも壊れず動作する。
    """
    raw = getattr(product, "external_eval_payload", None)
    if not raw:
        return None

    obj = None
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        obj = None

    payload = None
    request_id = None

    if isinstance(obj, dict):
        request_id = obj.get("request_id")

        maybe_payload = obj.get("payload")
        if isinstance(maybe_payload, dict):
            payload = maybe_payload
        else:
            # payload直の場合
            if any(k in obj for k in ("matrix_matches_top", "followup_questions", "transaction_id")):
                payload = obj

    if not isinstance(payload, dict):
        return {"request_id": request_id, "top": None, "followup_questions": []}

    # matrix_matches_top[0] から根拠を抜く
    top = None
    mm = payload.get("matrix_matches_top")
    if isinstance(mm, list) and len(mm) > 0 and isinstance(mm[0], dict):
        first = mm[0]
        ev = first.get("evidence") or {}
        if not isinstance(ev, dict):
            ev = {}

        top = {
            "rule_item_no": ev.get("rule_item_no"),
            "rule_title": ev.get("rule_title") or ev.get("title"),
            "match_score": first.get("match_score"),
            "decision": (first.get("decision") or ev.get("decision")),
            "matched_tokens": ev.get("matched_tokens") if isinstance(ev.get("matched_tokens"), list) else [],
            "usage_text": ev.get("usage_text") or ev.get("usage_requirement_text") or "",
            "rule_snippet": ev.get("rule_snippet") or "",
        }

    # followup_questions
    followup_questions = []
    fq = payload.get("followup_questions")
    if isinstance(fq, list):
        followup_questions = [str(x) for x in fq if x]

    return {
        "request_id": request_id,
        "top": top,
        "followup_questions": followup_questions,
    }


# =========================
# 外部AI payload → 判定明細リスト
# =========================
def build_external_ai_items(product: Product) -> list[dict]:
    """matrix_matches_top の全アイテムを [{rule_item_no, decision, match_score}] で返す。"""
    raw = getattr(product, "external_eval_payload", None)
    if not raw:
        return []
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return []

    payload = None
    if isinstance(obj, dict):
        maybe = obj.get("payload")
        if isinstance(maybe, dict):
            payload = maybe
        elif any(k in obj for k in ("matrix_matches_top", "followup_questions", "transaction_id")):
            payload = obj

    if not isinstance(payload, dict):
        return []

    mm = payload.get("matrix_matches_top")
    if not isinstance(mm, list):
        return []

    items = []
    for m in mm:
        if not isinstance(m, dict):
            continue
        ev = m.get("evidence") or {}
        if not isinstance(ev, dict):
            ev = {}
        items.append({
            "rule_item_no": ev.get("rule_item_no") or m.get("rule_item_no") or "",
            "decision": m.get("decision") or ev.get("decision") or "",
            "match_score": m.get("match_score"),
        })
    return items


# =========================
# Routes
# =========================

@router.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse(url="/products", status_code=307)


@router.get("/products", response_class=HTMLResponse)
def list_products(request: Request, db: Session = Depends(get_db)):
    products = db.query(Product).order_by(Product.id.desc()).all()
    return templates.TemplateResponse("product_list.html", {"request": request, "products": products})


@router.get("/products/new", response_class=HTMLResponse)
def new_product_form(request: Request):
    return templates.TemplateResponse("product_new.html", {"request": request})


@router.post("/products/new")
def create_product(
    code: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    product = Product(code=code, name=name, description=description or None)
    db.add(product)
    db.commit()
    db.refresh(product)
    return RedirectResponse(url="/products", status_code=303)


@router.get("/products/{product_id}/edit", response_class=HTMLResponse)
def edit_product(request: Request, product_id: int, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # --- BOM 展開 ---
    bom_components = []
    bom_total_ratio = 0.0
    bom_total_cost = 0.0

    if product.bom_json:
        try:
            bom_items = json.loads(product.bom_json)
        except Exception:
            bom_items = []

        for item in bom_items:
            kind = item.get("kind", "material")
            note = item.get("note") or ""

            if kind == "material":
                comp_id = item.get("component_id")
                comp = db.get(Product, comp_id) if comp_id else None

                ratio = float(item.get("ratio") or 0.0)

                if comp:
                    std_price = float(comp.std_price or 0.0)
                    code = comp.code
                    name = comp.name
                    item_class = comp.item_class or ""
                else:
                    std_price = 0.0
                    code = item.get("component_code", "")
                    name = item.get("component_name") or item.get("description") or ""
                    item_class = ""

                cost = std_price * ratio
                bom_total_ratio += ratio
                bom_total_cost += cost

                bom_components.append(
                    {
                        "kind": "material",
                        "id": comp.id if comp else None,
                        "code": code,
                        "name": name,
                        "item_class": item_class,
                        "ratio": ratio,
                        "std_price": std_price,
                        "cost": cost,
                        "note": note,
                        "coo": item.get("coo"),
                        "unit_value": item.get("unit_value"),
                    }
                )

            elif kind == "overhead":
                amount = float(item.get("amount") or item.get("cost") or 0.0)
                desc = item.get("description", "Overhead")

                bom_total_cost += amount

                bom_components.append(
                    {
                        "kind": "overhead",
                        "id": None,
                        "code": "",
                        "name": desc,
                        "item_class": "Overhead",
                        "ratio": None,
                        "std_price": amount,
                        "cost": amount,
                        "note": note,
                        "coo": None,
                        "unit_value": None,
                    }
                )

    # --- 外部AIの要約（根拠/アクション） ---
    external_ai_summary = build_external_ai_summary(product)

    # --- HS判定結果のパース ---
    hs_candidates = []
    if product.hs_classification_result:
        try:
            hs_candidates = json.loads(product.hs_classification_result)
        except Exception:
            hs_candidates = []

    # --- 輸出管理 判定明細のパース ---
    ec_items: list[dict] = []
    raw_ec = getattr(product, "export_control_items", None)
    if raw_ec:
        try:
            ec_items = json.loads(raw_ec)
            if not isinstance(ec_items, list):
                ec_items = []
        except Exception:
            ec_items = []

    return templates.TemplateResponse(
        "product_edit.html",
        {
            "request": request,
            "product": product,
            "bom_components": bom_components,
            "bom_total_ratio": bom_total_ratio,
            "bom_total_cost": bom_total_cost,
            "external_ai_summary": external_ai_summary,
            "hs_candidates": hs_candidates,
            "export_control_items": ec_items,
        },
    )


@router.post("/products/{product_id}/edit")
def update_product(
    product_id: int,
    code: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    hs_code: str = Form(""),
    eccn: str = Form(""),
    ghs_signal_word: str = Form(""),
    ghs_pictograms: str = Form(""),
    ghs_h_statements: str = Form(""),
    ghs_p_statements: str = Form(""),
    ghs_classes: str = Form(""),
    item_class: str = Form(""),
    std_price: float = Form(None),
    regulation_ai_raw: str = Form(""),
    is_poison: bool = Form(False),
    is_deleterious: bool = Form(False),
    is_kashinho: bool = Form(False),
    is_kashinho_class_I: bool = Form(False),
    is_kashinho_class_II: bool = Form(False),
    is_roudou_anzen_eisei: bool = Form(False),
    is_prtr: bool = Form(False),
    is_shoubouho: bool = Form(False),
    is_high_pressure_gas: bool = Form(False),
    country_of_origin: str = Form(""),
    db: Session = Depends(get_db),
):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    product.code = code
    product.name = name
    product.description = description or None
    product.hs_code = hs_code or None
    product.eccn = eccn or None
    coo_val = country_of_origin.strip().upper()
    product.country_of_origin = coo_val[:2] if coo_val else None
    product.ghs_signal_word = ghs_signal_word or None
    product.ghs_pictograms = ghs_pictograms or None
    product.ghs_h_statements = ghs_h_statements or None
    product.ghs_p_statements = ghs_p_statements or None
    product.ghs_classes = ghs_classes or None

    product.item_class = item_class or None
    product.std_price = std_price

    product.is_poison = is_poison
    product.is_deleterious = is_deleterious
    product.is_kashinho = is_kashinho
    product.is_kashinho_class_I = is_kashinho_class_I
    product.is_kashinho_class_II = is_kashinho_class_II
    product.is_roudou_anzen_eisei = is_roudou_anzen_eisei
    product.is_prtr = is_prtr
    product.is_shoubouho = is_shoubouho
    product.is_high_pressure_gas = is_high_pressure_gas

    product.regulation_ai_raw = regulation_ai_raw or None

    db.commit()

    return RedirectResponse(url=f"/products/{product_id}/edit", status_code=303)


# =========================
# 追加: External AI integration (UI向け)
# =========================

@router.post("/products/{product_id}/external/request")
async def request_external_ai(product_id: int, db: Session = Depends(get_db)):
    """
    UI（品目編集画面）から、外部AIへ判定依頼。
    - 成功/失敗に関わらずアプリが落ちない
    - 実行後は品目編集画面へリダイレクト
    """
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # AI側仕様に合わせたペイロード（usage_summary優先、なければdescription）
    payload = {
        "product_id": product.id,
        "code": product.code,
        "name": product.name,
        "description": product.description or "",
        "usage_summary": product.usage_summary or "",
        "callback_webhook": settings.PUBLIC_WEBHOOK_URL,  # UI側Webhook
        # 任意（将来拡張）
        "hs_code": product.hs_code,
        "eccn": product.eccn,
        "item_class": product.item_class,
        "bom_json": product.bom_json,
        "regulation_ai_raw": product.regulation_ai_raw,
    }

    client = ExternalAppClient()

    try:
        resp = await client.request_export_control(payload)

        product.external_eval_requested_at = datetime.utcnow()
        product.external_eval_status = (resp.get("status") or "queued")
        product.external_eval_reason = "External request accepted (queued)."

        # request_id をDBカラム追加せずに保持したいので payload文字列に包んで保存
        product.external_eval_payload = json.dumps(
            {
                "request_id": resp.get("request_id"),
                "received_at": resp.get("received_at"),
                "status": resp.get("status", "queued"),
            },
            ensure_ascii=False,
        )

        db.commit()

    except httpx.ConnectError:
        product.external_eval_requested_at = datetime.utcnow()
        product.external_eval_status = "queued"
        product.external_eval_reason = "External app is not reachable (connection failed)."
        db.commit()

    except httpx.HTTPError as e:
        product.external_eval_requested_at = datetime.utcnow()
        product.external_eval_status = "error"
        product.external_eval_reason = f"External app HTTP error: {type(e).__name__}"
        db.commit()

    return RedirectResponse(url=f"/products/{product_id}/edit", status_code=303)


@router.post("/products/{product_id}/external/manual")
def update_external_ai_manual(
    product_id: int,
    status: str = Form(""),
    reason: str = Form(""),
    payload: str = Form(""),
    db: Session = Depends(get_db),
):
    """
    外部AI判定結果を手動で保存（画面から編集できるようにする）
    - status/reason/payload を Product に保存
    - 保存後は編集画面へ戻す
    """
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    product.external_eval_status = status or None
    product.external_eval_reason = reason or None
    product.external_eval_payload = payload or None
    product.external_eval_received_at = datetime.utcnow()

    db.commit()
    return RedirectResponse(url=f"/products/{product_id}/edit", status_code=303)


@router.post("/products/{product_id}/usage-summary")
def save_usage_summary(
    product_id: int,
    usage_summary: str = Form(""),
    db: Session = Depends(get_db),
):
    """
    用途概要（外部AI判定用テキスト）を保存する。
    """
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    product.usage_summary = usage_summary.strip() or None
    db.commit()

    return RedirectResponse(url=f"/products/{product_id}/edit", status_code=303)


# =========================
# BOM upload + history
# =========================

@router.get("/products/{product_id}/bom", response_class=HTMLResponse)
def bom_upload_form(request: Request, product_id: int, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return templates.TemplateResponse("bom_upload.html", {"request": request, "product": product})


@router.post("/products/{product_id}/bom")
def bom_upload(
    product_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    content = file.file.read().decode("utf-8")
    df = pd.read_csv(io.StringIO(content))

    has_kind = "kind" in df.columns
    has_description = "description" in df.columns
    has_amount = "amount" in df.columns

    bom_items = []
    total_cost = 0.0
    total_ratio = 0.0

    has_coo = "coo" in df.columns
    has_unit_value = "unit_value" in df.columns

    for _, row in df.iterrows():
        kind = "material"
        if has_kind and not pd.isna(row.get("kind", "")):
            kind = str(row.get("kind", "")).strip().lower() or "material"

        note = str(row.get("note", "") or "")

        if kind == "material":
            comp_code = str(row.get("component_code", "")).strip()
            if not comp_code:
                raise HTTPException(status_code=400, detail="material 行では component_code が必須です。")

            try:
                raw_ratio = float(row.get("ratio", 0.0))
            except Exception:
                raise HTTPException(status_code=400, detail=f"component_code={comp_code} の ratio が数値として解釈できません。")

            ratio = raw_ratio if raw_ratio <= 1.0 else raw_ratio / 100.0

            if has_description and not pd.isna(row.get("description", "")):
                description = str(row.get("description", "") or "")
            elif "component_name" in df.columns:
                description = str(row.get("component_name", "") or "")
            else:
                description = ""

            # 再輸出規制用: 原産地 (ISO 3166-1 alpha-2) と単価USD
            coo_raw = str(row.get("coo", "") or "").strip().upper() if has_coo else ""
            coo = coo_raw[:2] if coo_raw else None

            try:
                unit_value = float(row.get("unit_value", 0.0) or 0.0) if has_unit_value else None
            except Exception:
                unit_value = None

            comp = db.query(Product).filter(Product.code == comp_code).first()
            if not comp:
                raise HTTPException(status_code=400, detail=f"構成品目コードが未登録です: {comp_code}")

            comp_price = float(comp.std_price or 0.0)
            cost = comp_price * ratio

            bom_item: dict = {
                "kind": "material",
                "component_id": comp.id,
                "component_code": comp.code,
                "component_name": comp.name,
                "description": description,
                "ratio": ratio,
                "note": note,
            }
            if coo is not None:
                bom_item["coo"] = coo
            if unit_value is not None:
                bom_item["unit_value"] = unit_value

            bom_items.append(bom_item)

            total_ratio += ratio
            total_cost += cost

        elif kind == "overhead":
            if not (has_description and has_amount):
                raise HTTPException(status_code=400, detail="kind=overhead の行を使う場合は description と amount 列が必要です。")

            description = str(row.get("description", "") or "")
            try:
                amount = float(row.get("amount", 0.0))
            except Exception:
                raise HTTPException(status_code=400, detail=f"overhead '{description}' の amount が数値として解釈できません。")

            bom_items.append({"kind": "overhead", "description": description, "amount": amount, "note": note})
            total_cost += amount

        else:
            raise HTTPException(status_code=400, detail=f"kind の値が不正です: {kind}（material / overhead が利用可能）")

    bom_json_str = json.dumps(bom_items, ensure_ascii=False)

    product.bom_json = bom_json_str
    product.std_price = total_cost

    max_version = (
        db.query(func.max(BomHistory.version))
        .filter(BomHistory.product_id == product_id)
        .scalar()
    )
    next_version = (max_version or 0) + 1

    history = BomHistory(
        product_id=product_id,
        version=next_version,
        bom_json=bom_json_str,
        total_ratio=total_ratio,
        total_cost=total_cost,
        note=f"CSV upload: {file.filename}",
    )
    db.add(history)

    db.commit()
    return RedirectResponse(url=f"/products/{product_id}/edit", status_code=303)


@router.get("/products/{product_id}/bom/history", response_class=HTMLResponse)
def bom_history_list(request: Request, product_id: int, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    histories = (
        db.query(BomHistory)
        .filter(BomHistory.product_id == product_id)
        .order_by(BomHistory.version.desc())
        .all()
    )

    return templates.TemplateResponse(
        "bom_history_list.html",
        {"request": request, "product": product, "histories": histories},
    )


@router.get("/products/{product_id}/bom/history/{history_id}", response_class=HTMLResponse)
def bom_history_detail(request: Request, product_id: int, history_id: int, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    history = (
        db.query(BomHistory)
        .filter(BomHistory.id == history_id, BomHistory.product_id == product_id)
        .first()
    )
    if not history:
        raise HTTPException(status_code=404, detail="History not found")

    bom_components = []
    bom_total_ratio = 0.0
    bom_total_cost = 0.0

    try:
        bom_items = json.loads(history.bom_json)
    except Exception:
        bom_items = []

    for item in bom_items:
        kind = item.get("kind", "material")
        note = item.get("note") or ""

        if kind == "material":
            comp_id = item.get("component_id")
            comp = db.get(Product, comp_id) if comp_id else None
            ratio = float(item.get("ratio") or 0.0)

            if comp:
                std_price = float(comp.std_price or 0.0)
                code = comp.code
                name = comp.name
                item_class = comp.item_class or ""
            else:
                std_price = 0.0
                code = item.get("component_code", "")
                name = item.get("component_name") or item.get("description") or ""
                item_class = ""

            cost = std_price * ratio
            bom_total_ratio += ratio
            bom_total_cost += cost

            bom_components.append(
                {
                    "kind": "material",
                    "id": comp.id if comp else None,
                    "code": code,
                    "name": name,
                    "item_class": item_class,
                    "ratio": ratio,
                    "std_price": std_price,
                    "cost": cost,
                    "note": note,
                }
            )

        elif kind == "overhead":
            amount = float(item.get("amount") or item.get("cost") or 0.0)
            desc = item.get("description", "Overhead")

            bom_total_cost += amount
            bom_components.append(
                {
                    "kind": "overhead",
                    "id": None,
                    "code": "",
                    "name": desc,
                    "item_class": "Overhead",
                    "ratio": None,
                    "std_price": amount,
                    "cost": amount,
                    "note": note,
                }
            )

    return templates.TemplateResponse(
        "bom_history_detail.html",
        {
            "request": request,
            "product": product,
            "history": history,
            "bom_components": bom_components,
            "bom_total_ratio": bom_total_ratio,
            "bom_total_cost": bom_total_cost,
        },
    )


# =========================
# Export evaluation (internal)
# =========================
@router.post("/products/{product_id}/export/evaluate")
def run_export_evaluation(product_id: int, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    result = evaluate_export_control(product, db)

    product.export_control_status = result["status"]
    product.export_control_reason = result["reason"]
    product.export_control_checked_at = datetime.utcnow()

    db.commit()
    return RedirectResponse(url=f"/products/{product_id}/edit", status_code=303)


@router.post("/products/{product_id}/export/manual")
def update_export_control_manual(
    product_id: int,
    status: str = Form(...),
    reason: str = Form(""),
    db: Session = Depends(get_db),
):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    allowed_status = [
        "not_evaluated",
        "non_controlled",
        "needs_attention",
        "to_be_checked",
        "controlled",
    ]
    if status not in allowed_status:
        raise HTTPException(status_code=400, detail="不正なステータスです。")

    product.export_control_status = status
    product.export_control_reason = reason or None
    product.export_control_checked_at = datetime.utcnow()

    db.commit()
    return RedirectResponse(url=f"/products/{product_id}/edit", status_code=303)


@router.post("/products/{product_id}/export/items")
def save_export_items(
    product_id: int,
    status: str = Form(...),
    export_reason: str = Form(""),
    items_json: str = Form("[]"),
    db: Session = Depends(get_db),
):
    """輸出管理 判定記録（全体ステータス＋明細テーブル）を保存する。"""
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    allowed_status = [
        "not_evaluated", "non_controlled", "needs_attention", "to_be_checked", "controlled",
    ]
    if status not in allowed_status:
        raise HTTPException(status_code=400, detail="不正なステータスです。")

    try:
        items = json.loads(items_json)
        if not isinstance(items, list):
            items = []
    except Exception:
        items = []

    product.export_control_status = status
    product.export_control_reason = export_reason or None
    product.export_control_checked_at = datetime.utcnow()
    product.export_control_items = json.dumps(items, ensure_ascii=False)

    db.commit()
    return RedirectResponse(url=f"/products/{product_id}/edit", status_code=303)


# =========================
# CSV import
# =========================
@router.get("/products/import", response_class=HTMLResponse)
def import_products_page(request: Request):
    return templates.TemplateResponse("products_import.html", {"request": request})


@router.post("/products/import", response_class=HTMLResponse)
def import_products(file: UploadFile = File(...), db: Session = Depends(get_db)):
    try:
        content = file.file.read().decode("utf-8")
        df = pd.read_csv(io.StringIO(content))

        required_columns = ["code", "name", "item_class", "std_price"]
        for col in required_columns:
            if col not in df.columns:
                raise HTTPException(status_code=400, detail=f"CSVに {col} 列がありません。")

        for _, row in df.iterrows():
            code = str(row["code"]).strip()
            if not code:
                continue

            name = str(row["name"]).strip()
            item_class = str(row["item_class"]).strip() or None

            try:
                std_price = float(row["std_price"])
            except Exception:
                std_price = None

            product = db.query(Product).filter(Product.code == code).first()
            if product:
                product.name = name
                product.item_class = item_class
                product.std_price = std_price
            else:
                db.add(Product(code=code, name=name, item_class=item_class, std_price=std_price))

        db.commit()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return RedirectResponse(url="/products", status_code=303)


# =========================
# HSコード候補 ステータス確認（モーダルポーリング用）
# =========================

@router.get("/products/{product_id}/hs/status")
def get_hs_status(product_id: int, db: Session = Depends(get_db)):
    """HSコード判定のステータスと結果をJSONで返す（モーダルポーリング用）。"""
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    candidates = []
    if product.hs_classification_result:
        try:
            candidates = json.loads(product.hs_classification_result)
        except Exception:
            candidates = []

    return {
        "status": product.hs_classification_status or "not_started",
        "classified_at": product.hs_classified_at,
        "candidates": candidates,
    }


# =========================
# HSコード候補 適用
# =========================

@router.post("/products/{product_id}/hs/apply")
def apply_hs_code(
    product_id: int,
    hs_code: str = Form(...),
    db: Session = Depends(get_db),
):
    """HS判定候補から選択したコードを product.hs_code に反映する。"""
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    product.hs_code = hs_code.strip()
    db.commit()
    return RedirectResponse(url=f"/products/{product_id}/edit", status_code=303)
