"""Dataset admin router.

エンドポイント:
- GET  /admin/datasets              → データセット管理 UI
- POST /admin/datasets/upload       → ファイルアップロード (raw/)
- DELETE /admin/datasets/files/{source_type}/{filename}  → ファイル削除
- GET  /admin/datasets/api/status   → 全ソースの状態 JSON
- POST /admin/datasets/build        → control_nodes.json ビルド実行
"""

import pathlib
import shutil
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from platform_core.services.dataset_builder import builder as _builder
from platform_core.services.dataset_builder import ingest as _ingest

router = APIRouter(tags=["datasets"])

_TEMPLATES_DIR = pathlib.Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# プロジェクトルート = platform-core/platform_core/routers/ の 4 段上
_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.parent
_SOURCE_ROOT  = _PROJECT_ROOT / "data" / "source"
_UNIFIED_DIR  = _PROJECT_ROOT / "data" / "unified"

# ── ソースタイプ定義 ────────────────────────────────────────────
SOURCE_TYPES: list[dict] = [
    {
        "key":         "fefta",
        "label":       "外為法 別表第1",
        "description": "輸出貿易管理令 別表第1（経産省）。Excel / CSV 形式。",
        "icon":        "🇯🇵",
        "accept":      ".xlsx,.xls,.csv",
        "hint":        "bekkan1_2024.xlsx など",
        "source_url":  "https://www.meti.go.jp/policy/anpo/law_document.html",
    },
    {
        "key":         "eccn",
        "label":       "BIS CCL (ECCN)",
        "description": "米国 Commerce Control List（BIS）。XML / CSV 形式。",
        "icon":        "🇺🇸",
        "accept":      ".xml,.csv",
        "hint":        "part774_2024.xml など",
        "source_url":  "https://www.ecfr.gov/current/title-15/subtitle-B/chapter-VII/subchapter-C/part-774",
    },
    {
        "key":         "hs",
        "label":       "HS-外為法-ECCN 対照表",
        "description": "経産省 HS/外為法対照表 + 米 CBP Schedule B。Excel / CSV。",
        "icon":        "🔗",
        "accept":      ".xlsx,.xls,.csv",
        "hint":        "meti_hs_fefta_2024.xlsx / cbp_schedule_b_2024.csv など",
        "source_url":  "https://www.meti.go.jp/policy/anpo/law_document.html",
    },
    {
        "key":         "patents",
        "label":       "特許データ",
        "description": "J-PlatPat / Google Patents CSV。IPC 分類で絞り込んだもの。用途推定の核心。",
        "icon":        "📄",
        "accept":      ".csv,.json,.jsonl",
        "hint":        "jplatpat_G03F7_2024.csv など（IPC 別）",
        "source_url":  "https://www.j-platpat.inpit.go.jp/",
    },
    {
        "key":         "wassenaar",
        "label":       "Wassenaar Arrangement",
        "description": "WA Sensitive / Very Sensitive List。PDF を OCR 変換した CSV 等。",
        "icon":        "🌐",
        "accept":      ".csv,.txt,.json",
        "hint":        "wa_sensitive_list_2023.csv など",
        "source_url":  "https://www.wassenaar.org/control-lists/",
    },
]

_SOURCE_INDEX: dict[str, dict] = {s["key"]: s for s in SOURCE_TYPES}


def _source_dir(source_type: str) -> pathlib.Path:
    return _SOURCE_ROOT / source_type / "raw"


def _list_files(source_type: str) -> list[dict]:
    d = _source_dir(source_type)
    d.mkdir(parents=True, exist_ok=True)
    files = []
    for f in sorted(d.iterdir()):
        if f.is_file():
            stat = f.stat()
            files.append({
                "name":     f.name,
                "size_kb":  round(stat.st_size / 1024, 1),
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
    return files


def _unified_status() -> dict:
    node_file = _UNIFIED_DIR / "control_nodes.json"
    manifest  = _UNIFIED_DIR / "build_manifest.json"
    if node_file.exists():
        stat = node_file.stat()
        return {
            "exists":   True,
            "size_kb":  round(stat.st_size / 1024, 1),
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "manifest": manifest.exists(),
        }
    return {"exists": False}


# ── ルート ──────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse, include_in_schema=False)
async def datasets_ui(request: Request):
    """データセット管理 UI。"""
    source_status = []
    for s in SOURCE_TYPES:
        files = _list_files(s["key"])
        source_status.append({**s, "files": files, "file_count": len(files)})

    unified = _unified_status()

    return templates.TemplateResponse(
        "datasets_admin.html",
        {
            "request":       request,
            "sources":       source_status,
            "unified":       unified,
            "flash":         request.query_params.get("flash"),
            "flash_type":    request.query_params.get("flash_type", "ok"),
        },
    )


@router.post("/upload", include_in_schema=False)
async def upload_source_file(
    request:     Request,
    source_type: Annotated[str, Form()],
    file:        Annotated[UploadFile, File()],
):
    """ソースファイルをアップロードして raw/ に保存する。"""
    if source_type not in _SOURCE_INDEX:
        raise HTTPException(status_code=400, detail=f"Unknown source_type: {source_type}")
    if not file.filename:
        raise HTTPException(status_code=400, detail="ファイルが選択されていません")

    dest_dir  = _source_dir(source_type)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / file.filename

    with dest_file.open("wb") as fp:
        shutil.copyfileobj(file.file, fp)

    label = _SOURCE_INDEX[source_type]["label"]
    flash = f"{label} に「{file.filename}」をアップロードしました"
    return RedirectResponse(
        url=f"/admin/datasets?flash={flash}&flash_type=ok",
        status_code=303,
    )


@router.post("/files/{source_type}/{filename}/delete", include_in_schema=False)
async def delete_source_file(source_type: str, filename: str):
    """raw/ のファイルを削除する。"""
    if source_type not in _SOURCE_INDEX:
        raise HTTPException(status_code=400, detail="Unknown source_type")

    target = _source_dir(source_type) / filename
    # パストラバーサル防止
    if not target.resolve().is_relative_to(_source_dir(source_type).resolve()):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")

    target.unlink()
    label = _SOURCE_INDEX[source_type]["label"]
    flash = f"{label} から「{filename}」を削除しました"
    return RedirectResponse(
        url=f"/admin/datasets?flash={flash}&flash_type=warn",
        status_code=303,
    )


@router.get("/api/status", include_in_schema=False)
async def api_status():
    """全ソースの状態を JSON で返す（AJAX 用）。"""
    sources = {}
    for s in SOURCE_TYPES:
        files = _list_files(s["key"])
        sources[s["key"]] = {"file_count": len(files), "files": files}
    return JSONResponse({"sources": sources, "unified": _unified_status()})


@router.post("/ingest/patents", include_in_schema=False)
async def ingest_patents():
    """data/source/patents/raw/ の CSV を前処理して processed/ に書き出す。"""
    try:
        report = _ingest.run_ingest()
        total  = report.get("total_unique", 0)
        files  = len(report.get("files", []))
        flash  = f"特許CSV前処理完了 — {files} ファイル / {total} 件（ユニーク）を processed/ に書き出しました"
        flash_type = "ok"
        if report.get("errors"):
            flash += f"（エラー {len(report['errors'])} 件あり）"
            flash_type = "warn"
    except Exception as exc:
        flash      = f"前処理エラー: {exc}"
        flash_type = "warn"
    return RedirectResponse(
        url=f"/admin/datasets?flash={flash}&flash_type={flash_type}",
        status_code=303,
    )


@router.post("/build", include_in_schema=False)
async def build_control_nodes(request: Request):
    """control_nodes.json をビルドして data/unified/ に書き出す。"""
    try:
        manifest = _builder.build()
        stats    = manifest.get("stats", {})
        flash = (
            f"ビルド完了 — "
            f"規制ノード {stats.get('regulation_nodes', 0)} 件 / "
            f"Explicit edges {stats.get('explicit_edges', 0)} 件 / "
            f"Semantic edges {stats.get('semantic_edges', 0)} 件 "
            f"({manifest.get('duration_seconds', 0):.1f}s)"
        )
        return RedirectResponse(
            url=f"/admin/datasets?flash={flash}&flash_type=ok",
            status_code=303,
        )
    except Exception as exc:
        flash = f"ビルドエラー: {exc}"
        return RedirectResponse(
            url=f"/admin/datasets?flash={flash}&flash_type=warn",
            status_code=303,
        )
