"""J-Platpat 連携ルーター。

機能:
  GET  /jplatpat          → 一覧ページ（HTML）
  POST /jplatpat/import   → CSV アップロード & 出願番号登録
  POST /jplatpat/fetch/{id}  → 1件 API 取得
  POST /jplatpat/fetch-all   → 未取得分を一括バッチ取得
  GET  /jplatpat/{id}     → 詳細ページ（HTML）
  DELETE /jplatpat/{id}   → レコード削除
  GET  /api/jplatpat/list → JSON 一覧（テーブル用）
"""
from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime
from typing import Optional

import httpx

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.jplatpat_patent import JplatpatPatent
from app.services.jplatpat_service import (
    JplatpatAPIError,
    JplatpatAuthError,
    _normalize_app_number,
    fetch_app_progress,
    fetch_registration_info,
    fetch_cite_doc_info,
    fetch_jpp_fixed_address,
    fetch_from_google_patents,
    parse_progress_to_fields,
    parse_registration_to_fields,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# J-PlatPat CSV の出願番号カラム候補（優先順）
_APP_NUMBER_COLS = [
    "出願番号", "application_number", "ApplicationNumber",
    "出願No", "出願no", "AppNo",
]


# ─────────────────────────────────────────────────────────────────────
#  ページ: 一覧
# ─────────────────────────────────────────────────────────────────────

@router.get("/jplatpat", response_class=HTMLResponse)
async def jplatpat_list_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(JplatpatPatent).order_by(JplatpatPatent.imported_at.desc())
    )
    patents = result.scalars().all()
    parsed = [_parse_for_display(p) for p in patents]

    stats = {
        "total":        len(patents),
        "completed":    sum(1 for p in patents if p.fetch_status == "completed"),
        "pending":      sum(1 for p in patents if p.fetch_status == "pending"),
        "failed":       sum(1 for p in patents if p.fetch_status == "failed"),
        "fulltext":     sum(1 for p in patents if p.full_text_fetched_at is not None),
        "need_fulltext": sum(
            1 for p in patents
            if p.fetch_status == "completed" and p.full_text_fetched_at is None
        ),
    }

    return templates.TemplateResponse(
        "jplatpat_list.html",
        {"request": request, "patents": parsed, "stats": stats},
    )


# ─────────────────────────────────────────────────────────────────────
#  ページ: 詳細
# ─────────────────────────────────────────────────────────────────────

@router.get("/jplatpat/{patent_id}", response_class=HTMLResponse)
async def jplatpat_detail_page(
    request: Request,
    patent_id: int,
    db: AsyncSession = Depends(get_db),
):
    patent = await db.get(JplatpatPatent, patent_id)
    if not patent:
        raise HTTPException(status_code=404, detail="Patent not found")

    parsed = _parse_for_display(patent)
    # 生JSON をインデント表示用に展開
    progress_raw = {}
    if patent.progress_json:
        try:
            progress_raw = json.loads(patent.progress_json)
        except Exception:
            pass

    return templates.TemplateResponse(
        "jplatpat_detail.html",
        {"request": request, "patent": parsed, "progress_raw": progress_raw},
    )


# ─────────────────────────────────────────────────────────────────────
#  CSV インポート
# ─────────────────────────────────────────────────────────────────────

@router.post("/jplatpat/import")
async def import_csv(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    J-PlatPat からエクスポートした CSV を受け取り、出願番号を DB に登録する。
    すでに登録済みの出願番号はスキップ（重複なし）。
    """
    content = await file.read()
    # BOM 付き UTF-8 対応
    text = content.decode("utf-8-sig", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []

    # 出願番号カラムを特定
    app_col = _detect_app_number_col(headers)
    if not app_col:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "detail": (
                    f"出願番号カラムが見つかりません。"
                    f" CSV のヘッダー: {headers[:10]}。"
                    f" カラム名を「出願番号」にしてください。"
                ),
            },
        )

    added = 0
    skipped = 0
    rows: list[dict] = []
    for row in reader:
        raw = (row.get(app_col) or "").strip()
        if not raw:
            continue
        rows.append(row)

    for row in rows:
        raw = (row.get(app_col) or "").strip()
        normalized = _normalize_app_number(raw)
        if not normalized or not normalized.isdigit():
            skipped += 1
            continue

        # 重複チェック
        exists = await db.execute(
            select(JplatpatPatent).where(JplatpatPatent.app_number == normalized)
        )
        if exists.scalar_one_or_none():
            skipped += 1
            continue

        # CSV にタイトルが含まれている場合は事前セット
        title = (
            row.get("発明の名称") or row.get("Title") or
            row.get("title") or row.get("発明名称") or ""
        ).strip()

        patent = JplatpatPatent(
            app_number=normalized,
            app_number_raw=raw,
            title=title or None,
            fetch_status="pending",
            imported_at=datetime.utcnow(),
        )
        db.add(patent)
        added += 1

    await db.commit()
    return {"ok": True, "added": added, "skipped": skipped, "total_rows": len(rows)}


# ─────────────────────────────────────────────────────────────────────
#  1件 API 取得
# ─────────────────────────────────────────────────────────────────────

@router.post("/jplatpat/fetch/{patent_id}")
async def fetch_one(patent_id: int, db: AsyncSession = Depends(get_db)):
    """
    1件の特許について J-Platpat API を呼び出し、メタ情報を取得・保存する。
    """
    patent = await db.get(JplatpatPatent, patent_id)
    if not patent:
        raise HTTPException(status_code=404, detail="Patent not found")

    patent.fetch_status = "fetching"
    await db.commit()

    try:
        result = await _fetch_and_store(patent)
        await db.commit()
        return {"ok": True, "app_number": patent.app_number, **result}

    except (JplatpatAuthError, JplatpatAPIError) as e:
        patent.fetch_status = "failed"
        patent.fetch_error = str(e)
        await db.commit()
        return {"ok": False, "error": str(e)}

    except Exception as e:
        patent.fetch_status = "failed"
        patent.fetch_error = f"予期しないエラー: {type(e).__name__}: {e}"
        await db.commit()
        return {"ok": False, "error": patent.fetch_error}


# ─────────────────────────────────────────────────────────────────────
#  一括バッチ取得
# ─────────────────────────────────────────────────────────────────────

@router.post("/jplatpat/fetch-all")
async def fetch_all_pending(db: AsyncSession = Depends(get_db)):
    """
    fetch_status = 'pending' または 'failed' の全レコードを順番に API 取得する。
    レート制限（10req/min）は jplatpat_service 側で制御する。
    """
    result = await db.execute(
        select(JplatpatPatent).where(
            JplatpatPatent.fetch_status.in_(["pending", "failed"])
        ).order_by(JplatpatPatent.imported_at)
    )
    targets = result.scalars().all()

    if not targets:
        return {"ok": True, "message": "取得対象がありません", "processed": 0}

    succeeded = 0
    failed_list = []

    for patent in targets:
        patent.fetch_status = "fetching"
        await db.commit()
        try:
            await _fetch_and_store(patent)
            await db.commit()
            succeeded += 1
        except Exception as e:
            patent.fetch_status = "failed"
            patent.fetch_error = str(e)
            await db.commit()
            failed_list.append({"app_number": patent.app_number, "error": str(e)})

    return {
        "ok": True,
        "total": len(targets),
        "succeeded": succeeded,
        "failed": len(failed_list),
        "failed_list": failed_list[:10],  # 先頭10件のみ返す
    }


# ─────────────────────────────────────────────────────────────────────
#  削除
# ─────────────────────────────────────────────────────────────────────

@router.delete("/api/jplatpat/{patent_id}")
async def delete_jplatpat_patent(patent_id: int, db: AsyncSession = Depends(get_db)):
    patent = await db.get(JplatpatPatent, patent_id)
    if not patent:
        raise HTTPException(status_code=404, detail="Patent not found")
    await db.delete(patent)
    await db.commit()
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────
#  Google Patents 全文取得（1件）
# ─────────────────────────────────────────────────────────────────────

@router.post("/jplatpat/fetch-fulltext/{patent_id}")
async def fetch_fulltext_one(patent_id: int, db: AsyncSession = Depends(get_db)):
    """
    Google Patents からIPC・要約・請求項・明細書を取得し保存する。
    J-PlatPat 書類 API (#8) の代替実装。
    """
    patent = await db.get(JplatpatPatent, patent_id)
    if not patent:
        raise HTTPException(status_code=404, detail="Patent not found")

    try:
        result = await fetch_from_google_patents(
            pub_number=patent.publication_number or "",
            reg_number=patent.registration_number or "",
            ad_pub_number=_extract_ad_pub_number(patent),
        )

        if result["ipc_codes"]:
            patent.ipc_codes = json.dumps(result["ipc_codes"], ensure_ascii=False)
        if result["abstract"]:
            patent.abstract_ja = result["abstract"]
        if result["claims"]:
            patent.claims_xml = result["claims"]
        if result["description"]:
            patent.description_xml = result["description"]
        if result["google_doc_id"]:
            patent.google_doc_id = result["google_doc_id"]

        patent.full_text_fetched_at = datetime.utcnow()
        await db.commit()

        # ai_validation への自動投入（ベストエフォート）
        sync_result = await _push_to_ai_validation(patent)

        return {
            "ok": True,
            "ipc_codes": result["ipc_codes"],
            "source_url": result["source_url"],
            "has_abstract": bool(result["abstract"]),
            "has_claims": bool(result["claims"]),
            "synced_to_validation": sync_result.get("ok", False),
        }
    except Exception as e:
        await db.rollback()
        return {"ok": False, "error": str(e)}


@router.post("/jplatpat/fetch-fulltext-all")
async def fetch_fulltext_all(db: AsyncSession = Depends(get_db)):
    """
    fetch_status='completed' で全文未取得のレコードを一括 Google Patents 取得。
    """
    result = await db.execute(
        select(JplatpatPatent).where(
            JplatpatPatent.fetch_status == "completed",
            JplatpatPatent.full_text_fetched_at.is_(None),
            JplatpatPatent.publication_number.isnot(None),
        ).order_by(JplatpatPatent.imported_at)
    )
    targets = result.scalars().all()

    if not targets:
        return {"ok": True, "message": "取得対象がありません", "processed": 0}

    succeeded = 0
    failed_list = []
    for patent in targets:
        try:
            gp = await fetch_from_google_patents(
                pub_number=patent.publication_number or "",
                reg_number=patent.registration_number or "",
                ad_pub_number=_extract_ad_pub_number(patent),
            )
            if gp["ipc_codes"]:
                patent.ipc_codes = json.dumps(gp["ipc_codes"], ensure_ascii=False)
            if gp["abstract"]:
                patent.abstract_ja = gp["abstract"]
            if gp["claims"]:
                patent.claims_xml = gp["claims"]
            if gp["description"]:
                patent.description_xml = gp["description"]
            if gp["google_doc_id"]:
                patent.google_doc_id = gp["google_doc_id"]
            patent.full_text_fetched_at = datetime.utcnow()
            await db.commit()
            succeeded += 1
            # ai_validation への自動投入（ベストエフォート）
            await _push_to_ai_validation(patent)
        except Exception as e:
            await db.rollback()
            failed_list.append({"app_number": patent.app_number, "error": str(e)})

    return {
        "ok": True,
        "total": len(targets),
        "succeeded": succeeded,
        "failed": len(failed_list),
        "failed_list": failed_list[:5],
    }


_AI_VALIDATION_BASE = os.environ.get("AI_VALIDATION_BASE_URL", "http://localhost:8001")


async def _push_to_ai_validation(patent: JplatpatPatent) -> dict:
    """全文取得済み特許を ai_validation の patents テーブルに自動投入する。
    失敗してもメイン処理は止めない（ベストエフォート）。
    """
    ipc_list = []
    try:
        ipc_list = json.loads(patent.ipc_codes or "[]")
    except Exception:
        pass

    payload = {
        "publication_number": (
            patent.google_doc_id
            or patent.publication_number
            or f"JP-{patent.app_number}"
        ),
        "title":      patent.title or "",
        "assignee":   json.loads(patent.applicants or "[]")[0] if patent.applicants else "",
        "abstract":   patent.abstract_ja or "",
        "fulltext":   (patent.claims_xml or "") + "\n" + (patent.description_xml or ""),
        "ipc_codes":  ipc_list,
        "source_type": "jplatpat",
        "jplatpat_id": patent.id,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_AI_VALIDATION_BASE}/api/admin/patents/sync-one",
                json=payload,
            )
        return resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _extract_ad_pub_number(patent: JplatpatPatent) -> str:
    """progress_json から ADPublicationNumber を取り出す。"""
    if not patent.progress_json:
        return ""
    try:
        data = json.loads(patent.progress_json)
        return str(data.get("ADPublicationNumber") or "")
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────
#  JSON API: 一覧
# ─────────────────────────────────────────────────────────────────────

@router.get("/api/jplatpat/list")
async def list_jplatpat_patents(
    status: Optional[str] = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
):
    q = select(JplatpatPatent).order_by(JplatpatPatent.imported_at.desc()).limit(limit)
    if status:
        q = q.where(JplatpatPatent.fetch_status == status)
    result = await db.execute(q)
    patents = result.scalars().all()
    return {"patents": [_parse_for_display(p) for p in patents]}


# ─────────────────────────────────────────────────────────────────────
#  内部ヘルパー
# ─────────────────────────────────────────────────────────────────────

async def _fetch_and_store(patent: JplatpatPatent) -> dict:
    """
    app_progress / registration_info / jpp_fixed_address を呼び出し、
    patent オブジェクトのフィールドを更新する（commit は呼び出し元が行う）。
    """
    app_no = patent.app_number

    # 1. 経過情報（IPC・Fターム含む）
    progress_data = await fetch_app_progress(app_no)
    fields = parse_progress_to_fields(progress_data)

    patent.title              = fields["title"] or patent.title  # CSV 由来があれば保持
    patent.applicants         = json.dumps(fields["applicants"], ensure_ascii=False)
    patent.inventors          = json.dumps(fields["inventors"], ensure_ascii=False)
    patent.filing_date        = fields["filing_date"]
    patent.publication_number = fields["publication_number"] or None
    patent.publication_date   = fields["publication_date"]
    patent.registration_number = fields["registration_number"] or None
    patent.registration_date  = fields["registration_date"]
    patent.ipc_codes          = json.dumps(fields["ipc_codes"], ensure_ascii=False)
    patent.fi_codes           = json.dumps(fields["fi_codes"], ensure_ascii=False)
    patent.f_terms            = json.dumps(fields["f_terms"], ensure_ascii=False)
    patent.app_status         = fields["app_status"] or None
    patent.available_docs     = json.dumps(fields.get("available_docs", []), ensure_ascii=False)
    patent.progress_json      = json.dumps(fields["raw"], ensure_ascii=False)

    # 2. 登録情報（権利者・請求項数・存続期間）
    try:
        reg_data = await fetch_registration_info(app_no)
        reg_fields = parse_registration_to_fields(reg_data)
        patent.registration_number = reg_fields.get("registration_number") or patent.registration_number
        patent.registration_date   = reg_fields.get("registration_date") or patent.registration_date
        patent.right_persons       = json.dumps(
            reg_fields.get("right_persons", []), ensure_ascii=False
        )
        patent.number_of_claims    = reg_fields.get("number_of_claims") or None
        patent.expire_date         = reg_fields.get("expire_date")
        patent.registration_ipc    = json.dumps([], ensure_ascii=False)  # Phase 2
        patent.registration_json   = json.dumps(
            reg_fields.get("raw_registration", {}), ensure_ascii=False
        )
    except JplatpatAPIError:
        pass  # 未登録特許は 404 等が返る場合あり

    # 3. J-PlatPat 固定 URL
    try:
        patent.jpp_url = await fetch_jpp_fixed_address(app_no) or None
    except JplatpatAPIError:
        pass

    patent.fetch_status  = "completed"
    patent.fetch_error   = None
    patent.api_fetched_at = datetime.utcnow()

    return {
        "title": patent.title,
        "ipc_codes": fields["ipc_codes"],
        "f_terms": fields["f_terms"],
        "app_status": patent.app_status,
    }


def _detect_app_number_col(headers: list[str]) -> Optional[str]:
    """CSV ヘッダーから出願番号カラム名を検出する。"""
    for candidate in _APP_NUMBER_COLS:
        if candidate in headers:
            return candidate
    # 部分一致フォールバック
    for h in headers:
        if "出願" in h and ("番号" in h or "No" in h.lower()):
            return h
    return None


def _parse_for_display(p: JplatpatPatent) -> dict:
    """ORM オブジェクトを テンプレート/JSON 用 dict に変換する。"""
    def _loads(s: Optional[str]) -> list:
        if not s:
            return []
        try:
            return json.loads(s)
        except Exception:
            return []

    return {
        "id":                  p.id,
        "app_number":          p.app_number,
        "app_number_raw":      p.app_number_raw or p.app_number,
        "title":               p.title or "（未取得）",
        "applicants":          _loads(p.applicants),
        "inventors":           _loads(p.inventors),
        "filing_date":         p.filing_date or "",
        "publication_number":  p.publication_number or "",
        "publication_date":    p.publication_date or "",
        "registration_number": p.registration_number or "",
        "registration_date":   p.registration_date or "",
        "app_status":          p.app_status or "",
        "ipc_codes":           _loads(p.ipc_codes),
        "fi_codes":            _loads(p.fi_codes),
        "f_terms":             _loads(p.f_terms),
        "registration_ipc":    _loads(p.registration_ipc),
        "right_persons":       _loads(p.right_persons),
        "number_of_claims":    p.number_of_claims or "",
        "expire_date":         p.expire_date or "",
        "available_docs":      _loads(p.available_docs),
        "cited_docs":          _loads(p.cited_docs),
        "jpp_url":             p.jpp_url or "",
        "fetch_status":        p.fetch_status,
        "fetch_error":         p.fetch_error or "",
        "api_fetched_at":      p.api_fetched_at.isoformat() if p.api_fetched_at else "",
        "imported_at":         p.imported_at.isoformat() if p.imported_at else "",
        "note":                p.note or "",
        # Google Patents 全文
        "abstract_ja":         p.abstract_ja or "",
        "claims_text":         p.claims_xml or "",       # 請求項テキスト
        "description_text":    p.description_xml or "",  # 明細書テキスト（先頭）
        "google_doc_id":       p.google_doc_id or "",
        "full_text_fetched_at": p.full_text_fetched_at.isoformat() if p.full_text_fetched_at else "",
        "has_full_text":       bool(p.claims_xml or p.description_xml),
    }
