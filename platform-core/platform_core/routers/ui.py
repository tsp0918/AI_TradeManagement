"""ポータル UI ルーター。

エンドポイント:
- GET  /ui                      ホーム画面 (Jinja2)
- GET  /ui/platform             プラットフォーム管理ダッシュボード (HTML)
- GET  /ui/health/{module_key}  モジュールヘルスチェックプロキシ
- POST /ui/launch/{module_key}  モジュール起動 (uvicorn サブプロセス)
- POST /ui/stop/{module_key}    モジュール停止
"""

import asyncio
import os
import pathlib as _pathlib
import shutil as _shutil
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlparse

_shutil_which = _shutil.which

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.db.session import get_db
from platform_core.models.module_registry import ModuleRegistry
from platform_core.models.regulatory_change import RegulatoryChange

router = APIRouter(prefix="/ui", tags=["ui"])

_TEMPLATES_DIR = _pathlib.Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# ── アイコン定義 ───────────────────────────────────────────────
_MODULE_ICONS: dict[str, str] = {
    "platform-core":     "⚙️",
    "ai_validation":     "🔐",
    "ai_classification": "📦",
    "hs_classifier":     "🏷️",
    "rnd_assessment":    "🔬",
    "patent_search":     "📋",
    "dap":               "🔗",
    "screening":         "🛡️",
    "service_control":   "📜",
}

# 案件ダッシュボード（固定エントリ、最初に表示）
_DASHBOARD_ENTRY = {
    "key":               "dashboard",
    "name":              "案件ダッシュボード",
    "description":       "審査進行中案件の一覧と未完了アクション",
    "base_url":          "",
    "iframe_url":        "/ui/dashboard",
    "icon":              "📊",
    "health_check_path": "",
    "nav_section":       "admin",
}

# platform-core 自身の固定エントリ
_PLATFORM_ENTRY = {
    "key":               "platform-core",
    "name":              "プラットフォーム管理",
    "description":       "テナント・ユーザー・モジュール管理",
    "base_url":          "",
    "iframe_url":        "/ui/platform",
    "icon":              _MODULE_ICONS["platform-core"],
    "health_check_path": "/health",
    "nav_section":       "admin",
}

# ── 既知モジュール (DB 未登録時のフォールバック) ──────────────────
# モジュールが起動・登録済みの場合は DB 値が優先される。
# 順序 = 輸出管理フロー（業務プロセス順）→ DAP → platform-core
# ── nav_section 定義 ──────────────────────────────────────────────
# "flow"        : 業務フロー（開発・営業担当者が主に使う）
# "operations"  : 受注後・ERP連携業務（QC/QA・出荷・調達）
# "specialized" : 専門ツール（折りたたみ表示）
# "admin"       : 管理者・システム

_KNOWN_MODULES: list[dict] = [
    # ── 業務フロー（R&D起案 → 品目登録 → 該非判定 → スクリーニング → 取引審査）──
    {
        "key":               "rnd_assessment",
        "name":              "R&Dリスク評価",
        "description":       "R&Dプロジェクトの知財戦略と安全保障貿易管理の統合リスク評価",
        "base_url":          "http://localhost:8003",
        "iframe_url":        "/proxy/rnd_assessment/",
        "icon":              _MODULE_ICONS["rnd_assessment"],
        "health_check_path": "/health",
        "nav_section":       "flow",
    },
    {
        "key":               "ai_classification",
        "name":              "品目管理",
        "description":       "取り扱い品目の法規制情報管理・SDS解析・該非判定連携",
        "base_url":          "http://localhost:8002",
        "iframe_url":        "/proxy/ai_classification/",
        "icon":              _MODULE_ICONS["ai_classification"],
        "health_check_path": "/health",
        "nav_section":       "flow",
    },
    {
        "key":               "ai_validation",
        "name":              "AI取引審査",
        "description":       "外為法に基づく輸出管理の該非判定を AI で支援する",
        "base_url":          "http://localhost:8011",
        "iframe_url":        "/proxy/ai_validation/",
        "icon":              _MODULE_ICONS["ai_validation"],
        "health_check_path": "/health",
        "nav_section":       "flow",
    },
    {
        "key":               "service_control",
        "name":              "役務取引管理",
        "description":       "外為法 第25条 技術指導・ソフトウェア・クラウドサービス・みなし輸出の許可申請管理",
        "base_url":          "http://localhost:8011",
        "iframe_url":        "/proxy/ai_validation/ui/services",
        "icon":              _MODULE_ICONS["service_control"],
        "health_check_path": "/health",
        "nav_section":       "flow",
    },
    {
        "key":               "screening",
        "name":              "取引先スクリーニング",
        "description":       "BIS Entity List / OFAC SDN 等を用いた懸念取引先スクリーニング",
        "base_url":          "http://localhost:8005",
        "iframe_url":        "/proxy/screening/",
        "icon":              _MODULE_ICONS["screening"],
        "health_check_path": "/health",
        "nav_section":       "flow",
    },
    {
        "key":               "transaction_review",
        "name":              "取引審査キュー",
        "description":       "ERP 取引伝票・出荷伝票の輸出審査状況と手動承認キューを管理",
        "base_url":          "",
        "iframe_url":        "/ui/transaction-reviews",
        "icon":              "📝",
        "health_check_path": "",
        "nav_section":       "flow",
    },
    # ── 受注後・ERP連携業務（品目バージョン管理・出荷・調達）──────────────────
    {
        "key":               "item_version",
        "name":              "品目バージョン管理",
        "description":       "仕様変更履歴の追跡とコンプライアンス影響の自動アセスメント",
        "base_url":          "",
        "iframe_url":        "/ui/item-version",
        "icon":              "🔄",
        "health_check_path": "",
        "nav_section":       "operations",
    },
    {
        "key":               "export_license",
        "name":              "輸出許可申請",
        "description":       "EAR BIS-748P / 外為法 様式第1 のドラフト自動生成・期限管理",
        "base_url":          "",
        "iframe_url":        "/ui/export-license",
        "icon":              "📋",
        "health_check_path": "",
        "nav_section":       "operations",
    },
    {
        "key":               "supply_chain",
        "name":              "調達・BOM管理",
        "description":       "BOM 構造管理と EAR §734.4 De Minimis ルール自動計算・調達先リスク",
        "base_url":          "",
        "iframe_url":        "/ui/supply-chain",
        "icon":              "🔗",
        "health_check_path": "",
        "nav_section":       "operations",
    },
    {
        "key":               "compliance_lookup",
        "name":              "コンプライアンス進捗",
        "description":       "品目ごとの7ステージ進捗・未完了アクション・変化点タイムラインを一覧管理",
        "base_url":          "",
        "iframe_url":        "/ui/compliance-lookup",
        "icon":              "🗂️",
        "health_check_path": "",
        "nav_section":       "operations",
    },
    {
        "key":               "fta_check",
        "name":              "EPA/FTA 特恵税率",
        "description":       "日本が締結するEPA/FTA協定別の特恵関税率をHSコード×仕向地国で照会する",
        "base_url":          "",
        "iframe_url":        "/ui/fta-check",
        "icon":              "🌐",
        "health_check_path": "",
        "nav_section":       "operations",
    },
    # ── 専門ツール（折りたたみ表示）─────────────────────────────────────────
    {
        "key":               "patent_search",
        "name":              "AI特許検索",
        "description":       "Google Patents BigQuery を用いた特許検索と LLM による用途要件抽出",
        "base_url":          "http://localhost:8004",
        "iframe_url":        "/proxy/patent_search/",
        "icon":              _MODULE_ICONS["patent_search"],
        "health_check_path": "/health",
        "nav_section":       "specialized",
    },
    {
        "key":               "hs_classifier",
        "name":              "HSコード判定",
        "description":       "品目管理と連携し、HS2022 6桁コードを FAISS セマンティック検索で自動付番支援する",
        "base_url":          "http://localhost:8006",
        "iframe_url":        "/proxy/hs_classifier/",
        "icon":              _MODULE_ICONS["hs_classifier"],
        "health_check_path": "/health",
        "nav_section":       "specialized",
    },
    {
        "key":               "regime_check",
        "name":              "規制レジーム照合",
        "description":       "品目の ITAR/USML・EU Dual-Use・MTCR・NSG・AG 適用レジームを一括確認",
        "base_url":          "",
        "iframe_url":        "/ui/regime-check",
        "icon":              "🌐",
        "health_check_path": "",
        "nav_section":       "specialized",
    },
    {
        "key":               "counterparty",
        "name":              "与信管理",
        "description":       "取引先の与信スコア・制裁リスト・国別リスクを統合管理する",
        "base_url":          "",
        "iframe_url":        "/ui/counterparty",
        "icon":              "🏢",
        "health_check_path": "",
        "nav_section":       "specialized",
    },
    {
        "key":               "open_close_strategy",
        "name":              "オープンクローズ戦略",
        "description":       "特許出願 vs. 営業秘密の知財戦略を ECCN 感度・経済安保法リスクから判定",
        "base_url":          "http://localhost:8003",
        "iframe_url":        "/proxy/rnd_assessment/ui/open-close",
        "icon":              "🔓",
        "health_check_path": "/health",
        "nav_section":       "specialized",
    },
    {
        "key":               "icp_diagnosis",
        "name":              "ICP 自己診断",
        "description":       "CISTEC 8要素チェックリストによる社内コンプライアンスプログラム成熟度評価",
        "base_url":          "http://localhost:8011",
        "iframe_url":        "/proxy/ai_validation/ui/icp",
        "icon":              "📊",
        "health_check_path": "/health",
        "nav_section":       "specialized",
    },
    {
        "key":               "ontology_explorer",
        "name":              "オントロジーエクスプローラー",
        "description":       "ECCN/HS/FEFTA/IPC/F-term の知識グラフ探索・ライセンス判定・多ホップ経路検索",
        "base_url":          "",
        "iframe_url":        "/ui/ontology",
        "icon":              "🕸️",
        "health_check_path": "",
        "nav_section":       "specialized",
    },
    # ── 管理者・システム ─────────────────────────────────────────────────────
    {
        "key":               "dap",
        "name":              "DAP / AIオーケストレーター",
        "description":       "クロスモジュール AI エージェント・各画面コーチング・入力補助",
        "base_url":          "http://localhost:8010",
        "iframe_url":        "/proxy/dap/",
        "icon":              _MODULE_ICONS["dap"],
        "health_check_path": "/health",
        "nav_section":       "admin",
    },
    {
        "key":               "user_guide",
        "name":              "ユーザーガイド",
        "description":       "ユースケース別の使い方ガイドと全モジュール機能一覧",
        "base_url":          "",
        "iframe_url":        "/ui/user-guide",
        "icon":              "📖",
        "health_check_path": "",
        "nav_section":       "admin",
    },
]


def _build_module_entry(m: ModuleRegistry) -> dict:
    known = _KNOWN_INDEX.get(m.key, {})
    return {
        "key":              m.key,
        "name":             m.name,
        "description":      m.description or "",
        "base_url":         m.base_url,
        "iframe_url":       known.get("iframe_url", f"/proxy/{m.key}/"),
        "icon":             _MODULE_ICONS.get(m.key, "📌"),
        "health_check_path": m.health_check_path,
        "nav_section":      known.get("nav_section", "process"),
    }


_KNOWN_INDEX: dict[str, dict] = {m["key"]: m for m in _KNOWN_MODULES}


def _known_info(key: str) -> dict | None:
    """DB 未登録モジュールのフォールバック情報を返す。"""
    return _KNOWN_INDEX.get(key)


# ── サブプロセス管理 ────────────────────────────────────────────
_PROCESSES: dict[str, subprocess.Popen] = {}


def _project_root() -> Path:
    """ui.py から 4 段上がるとプロジェクトルート。"""
    return Path(__file__).parent.parent.parent.parent


def _module_port(base_url: str) -> int | None:
    return urlparse(base_url).port


def _port_in_use(port: int) -> bool:
    """指定ポートが既に使用中かどうかを確認する。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _start_module_subprocess(key: str, config: dict) -> None:
    """単一モジュールをサブプロセスで起動する。
    既にプロセスが生きているか、ポートが使用中の場合はスキップする。
    """
    # すでに管理中のプロセスが生きている場合はスキップ
    proc = _PROCESSES.get(key)
    if proc and proc.poll() is None:
        return

    port = _module_port(config["base_url"])
    if not port:
        return

    # ホットリロード等で前のプロセスがポートを保持している場合はスキップ
    if _port_in_use(port):
        return

    root       = _project_root()
    module_dir = root / "modules" / key
    uvicorn    = root / ".venv" / "bin" / "uvicorn"

    if not module_dir.is_dir() or not uvicorn.exists():
        return

    env = os.environ.copy()
    platform_core_dir = root / "platform-core"
    env["PYTHONPATH"] = f"{module_dir}{os.pathsep}{platform_core_dir}"

    proc = subprocess.Popen(
        [str(uvicorn), "app.main:app", "--host", "0.0.0.0", "--port", str(port)],
        cwd=str(module_dir),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _PROCESSES[key] = proc


def _start_ollama() -> None:
    """Ollama サーバーを起動する (既に起動中 / 未インストールの場合はスキップ)。"""
    if _port_in_use(11434):
        return  # 既に起動中
    ollama_bin = _shutil_which("ollama")
    if not ollama_bin:
        return  # Ollama が未インストール
    proc = subprocess.Popen(
        [ollama_bin, "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _PROCESSES["__ollama__"] = proc


def start_all_modules() -> None:
    """Ollama + 全既知モジュールをサブプロセスで並行起動する (fire-and-forget)。"""
    _start_ollama()
    for config in _KNOWN_MODULES:
        _start_module_subprocess(config["key"], config)


def stop_all_modules() -> None:
    """全モジュール・Ollama サブプロセスを終了する (platform-core 終了時)。"""
    for proc in _PROCESSES.values():
        if proc.poll() is None:
            proc.terminate()
    _PROCESSES.clear()


# ── ルーター ────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse, include_in_schema=False)
async def home(request: Request, db: AsyncSession = Depends(get_db)):
    """ポータルホーム画面。"""
    # 既知モジュールをベースにした順序付き辞書
    known: dict[str, dict] = {m["key"]: m for m in _KNOWN_MODULES}

    # DB 登録済みモジュールで上書き (base_url が環境変数で変わる場合に対応)
    result = await db.execute(
        select(ModuleRegistry)
        .where(ModuleRegistry.is_active == True)  # noqa: E712
        .order_by(ModuleRegistry.registered_at)
    )
    for m in result.scalars().all():
        known[m.key] = _build_module_entry(m)

    modules = [_DASHBOARD_ENTRY, _PLATFORM_ENTRY] + list(known.values())

    return templates.TemplateResponse(
        request, "home.html",
        {"modules": modules, "version": "0.1.0"},
    )


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """案件ダッシュボード — ai_validation から直近案件を取得して表示。"""
    ai_validation_url = os.environ.get("MODULE_AI_VALIDATION_URL", "http://localhost:8011")
    validation_public_url = os.environ.get("MODULE_AI_VALIDATION_PUBLIC_URL", "https://validation.tsp-aitrademanagement.com")
    transactions: list[dict] = []
    error: str | None = None

    all_orgs = request.query_params.get("all_orgs", "false").lower() in ("1", "true")
    x_org_id = request.headers.get("X-Organization-Id") or request.query_params.get("org_id")

    try:
        params = {"limit": 5}
        if all_orgs:
            params["all_orgs"] = "true"
        headers = {}
        if x_org_id:
            headers["X-Organization-Id"] = x_org_id
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{ai_validation_url}/api/transactions/recent",
                params=params,
                headers=headers,
            )
        if resp.status_code == 200:
            transactions = resp.json().get("transactions", [])
        else:
            error = f"ai_validation API エラー (HTTP {resp.status_code})"
    except Exception as exc:
        error = "AI取引審査モジュールに接続できません。起動しているか確認してください。"

    # ── 規制動向: 未読アラート (warn/danger) 最大5件 ──
    from sqlalchemy import or_, text as _text

    reg_alerts: list = []
    try:
        reg_q = (
            select(RegulatoryChange)
            .where(RegulatoryChange.is_dismissed == False)  # noqa: E712
            .where(RegulatoryChange.severity.in_(["warn", "danger"]))
            .order_by(RegulatoryChange.detected_at.desc())
            .limit(5)
        )
        if x_org_id:
            reg_q = reg_q.where(
                or_(
                    RegulatoryChange.relevant_org_ids.is_(None),
                    _text("relevant_org_ids @> jsonb_build_array(:oid)").bindparams(oid=x_org_id),
                )
            )
        reg_rows = await db.execute(reg_q)
        reg_alerts = [
            {
                "id":          rc.id,
                "title":       rc.title,
                "source":      rc.source,
                "severity":    rc.severity,
                "detected_at": rc.detected_at.strftime("%Y-%m-%d") if rc.detected_at else "",
                "source_url":  rc.source_url or "",
            }
            for rc in reg_rows.scalars().all()
        ]
    except Exception:
        pass

    return templates.TemplateResponse(
        request, "dashboard.html",
        {"transactions": transactions, "error": error,
         "reg_alerts": reg_alerts,
         "all_orgs": all_orgs, "x_org_id": x_org_id,
         "validation_public_url": validation_public_url},
    )


@router.get("/platform", response_class=HTMLResponse, include_in_schema=False)
async def platform_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """プラットフォーム管理ダッシュボード画面。"""
    result = await db.execute(
        select(ModuleRegistry).order_by(ModuleRegistry.registered_at)
    )
    registered = result.scalars().all()
    return templates.TemplateResponse(
        request, "platform_dashboard.html",
        {"registered_modules": registered, "version": "0.1.0"},
    )


@router.get("/health-status", include_in_schema=False)
async def all_module_health():
    """全既知モジュールのヘルス状態を一括取得する（ポーリング用）。"""
    # platform-core 自身 + 全既知モジュール
    targets = [
        {"key": "platform-core", "name": "プラットフォーム管理", "url": None},
    ] + [
        {
            "key": m["key"],
            "name": m["name"],
            "url": (m["base_url"].rstrip("/") + m["health_check_path"])
                    if m.get("base_url") and m.get("health_check_path") else None,
        }
        for m in _KNOWN_MODULES
    ]

    async def _check(t: dict) -> dict:
        if t["url"] is None:
            return {**t, "status": "online", "latency_ms": None}
        import time
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(t["url"])
            ms = round((time.monotonic() - t0) * 1000)
            if resp.status_code == 200:
                return {**t, "status": "online", "latency_ms": ms}
            return {**t, "status": "offline", "latency_ms": None, "code": resp.status_code}
        except Exception:
            return {**t, "status": "offline", "latency_ms": None}

    results = await asyncio.gather(*[_check(t) for t in targets])
    online = sum(1 for r in results if r["status"] == "online")
    return {"modules": results, "online": online, "total": len(results)}


@router.get("/health/{module_key}", include_in_schema=False)
async def module_health(module_key: str, db: AsyncSession = Depends(get_db)):
    """モジュールヘルスチェックのサーバー側プロキシ (CORS 回避)。"""
    if module_key in ("platform-core", "dashboard"):
        return {"status": "online"}

    # DB 優先、未登録の場合は既知モジュール設定で代替
    result = await db.execute(
        select(ModuleRegistry).where(ModuleRegistry.key == module_key)
    )
    module = result.scalar_one_or_none()
    if module is not None:
        base_url          = module.base_url
        health_check_path = module.health_check_path
    else:
        known = _known_info(module_key)
        if known is None:
            return {"status": "unknown"}
        base_url          = known["base_url"]
        health_check_path = known["health_check_path"]

    health_url = base_url.rstrip("/") + health_check_path
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(health_url)
        if resp.status_code == 200:
            return {"status": "online"}
        return {"status": "offline", "code": resp.status_code}
    except Exception:
        return {"status": "offline"}


@router.post("/launch/{module_key}", include_in_schema=False)
async def launch_module(module_key: str, db: AsyncSession = Depends(get_db)):
    """指定モジュールを uvicorn サブプロセスとして起動する。"""
    # 既存プロセスが生きていれば即返す
    proc = _PROCESSES.get(module_key)
    if proc and proc.poll() is None:
        return {"status": "already_running", "pid": proc.pid}

    # DB 優先、未登録の場合は既知モジュール設定で代替
    result = await db.execute(
        select(ModuleRegistry).where(ModuleRegistry.key == module_key)
    )
    module = result.scalar_one_or_none()
    if module is not None:
        base_url          = module.base_url
        health_check_path = module.health_check_path
    else:
        known = _known_info(module_key)
        if known is None:
            raise HTTPException(status_code=404, detail=f"Module '{module_key}' not found")
        base_url          = known["base_url"]
        health_check_path = known["health_check_path"]

    port = _module_port(base_url)
    if not port:
        raise HTTPException(status_code=400, detail="Cannot determine port from base_url")

    root       = _project_root()
    module_dir = root / "modules" / module_key
    uvicorn    = root / ".venv" / "bin" / "uvicorn"

    if not module_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"Module directory not found: {module_dir}")

    env = os.environ.copy()
    # platform_core は venv 未インストールのため module_dir と platform-core を両方含める
    platform_core_dir = root / "platform-core"
    env["PYTHONPATH"] = f"{module_dir}{os.pathsep}{platform_core_dir}"

    proc = subprocess.Popen(
        [str(uvicorn), "app.main:app", "--host", "0.0.0.0", "--port", str(port)],
        cwd=str(module_dir),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _PROCESSES[module_key] = proc

    # ヘルスチェックで起動完了を待つ (最大 10 秒)
    health_url = base_url.rstrip("/") + health_check_path
    for _ in range(20):
        await asyncio.sleep(0.5)
        if proc.poll() is not None:
            _PROCESSES.pop(module_key, None)
            return {"status": "failed", "returncode": proc.returncode}
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                r = await client.get(health_url)
            if r.status_code == 200:
                return {"status": "online", "pid": proc.pid}
        except Exception:
            pass

    return {"status": "launching", "pid": proc.pid}


@router.get("/counterparty", response_class=HTMLResponse, include_in_schema=False)
async def counterparty_ui(request: Request):
    """与信管理画面。"""
    return templates.TemplateResponse(request, "counterparty.html", {})


@router.get("/supply-chain", response_class=HTMLResponse, include_in_schema=False)
async def supply_chain_ui(request: Request):
    """サプライチェーン管理画面。"""
    return templates.TemplateResponse(request, "supply_chain.html", {})


@router.get("/export-license", response_class=HTMLResponse, include_in_schema=False)
async def export_license_ui(request: Request):
    """輸出許可申請管理画面。"""
    return templates.TemplateResponse(request, "export_license.html", {})


@router.get("/item-version", response_class=HTMLResponse, include_in_schema=False)
async def item_version_ui(request: Request):
    """品目バージョン管理・仕様変更コンプライアンス影響検知画面。"""
    return templates.TemplateResponse(request, "item_version.html", {})


@router.get("/transaction-reviews", response_class=HTMLResponse, include_in_schema=False)
async def transaction_review_ui(request: Request):
    """取引審査キュー画面。"""
    return templates.TemplateResponse(request, "transaction_review.html", {})


@router.get("/compliance-lookup", response_class=HTMLResponse, include_in_schema=False)
async def compliance_lookup_ui(request: Request):
    """コンプライアンス進捗管理・Lookup画面。"""
    return templates.TemplateResponse(request, "compliance_lookup.html", {})


@router.get("/regime-check", response_class=HTMLResponse, include_in_schema=False)
async def regime_check_ui(request: Request):
    """グローバル規制レジーム照合画面。"""
    return templates.TemplateResponse(request, "regime_check.html", {})


@router.get("/user-guide", response_class=HTMLResponse, include_in_schema=False)
async def user_guide_ui(request: Request):
    """ユーザーガイド画面。"""
    return templates.TemplateResponse(request, "user_guide.html", {})


@router.get("/ontology", response_class=HTMLResponse, include_in_schema=False)
async def ontology_explorer_ui(request: Request):
    """オントロジーエクスプローラー画面。"""
    return templates.TemplateResponse(request, "ontology_explorer.html", {})


@router.post("/stop/{module_key}", include_in_schema=False)
async def stop_module(module_key: str):
    """指定モジュールのサブプロセスを停止する。"""
    proc = _PROCESSES.pop(module_key, None)
    if not proc or proc.poll() is not None:
        return {"status": "not_running"}
    proc.terminate()
    return {"status": "stopped"}
