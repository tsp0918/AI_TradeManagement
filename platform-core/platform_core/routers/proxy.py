"""
platform_core/routers/proxy.py
──────────────────────────────
各モジュールへのリバースプロキシ。
外部クライアント（Cloudflare Tunnel経由）からモジュールUIにアクセスするため、
platform-core (port 8000) が /proxy/{module_key}/* を各モジュールの
localhost ポートに転送し、HTML/JS 中のルート相対 URL を書き換える。
"""

from __future__ import annotations

import re
from typing import Annotated

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/proxy", tags=["proxy"])

# モジュールキー → ローカルポートのマッピング
_MODULE_PORTS: dict[str, int] = {
    "ai_validation":    8011,
    "ai_classification": 8002,
    "rnd_assessment":   8003,
    "patent_search":    8004,
    "screening":        8005,
    "hs_classifier":    8006,
    "dap":              8010,
    "export_license":   8012,
    "trade_gate":       8013,
    "fta_origin":       8014,
}

# DAP chat-widget の localhost 参照を書き換えるための対応表
_LOCALHOST_REWRITES: dict[str, str] = {
    f"http://localhost:{port}": f"/proxy/{key}"
    for key, port in _MODULE_PORTS.items()
}

# Content-Type が書き換え対象かどうか
_REWRITE_CONTENT_TYPES = ("text/html", "application/javascript", "text/javascript")


def _rewrite_urls(content: str, proxy_prefix: str) -> str:
    """HTML/JS 内のルート相対 URL をプロキシパスに書き換える。"""
    # 1. HTML 属性: src="/...", href="/...", action="/..."
    content = re.sub(
        r'((?:src|href|action|data-src)=")(/(?!proxy/|/))',
        rf'\1{proxy_prefix}/',
        content,
    )
    # 2. JS fetch / axios (シングルクォート)
    content = re.sub(
        r"(fetch|axios\.get|axios\.post|axios\.put|axios\.delete|axios\.patch)\s*\(\s*'(/(?!proxy/))",
        rf"\1('{proxy_prefix}/",
        content,
    )
    # 3. JS fetch / axios (ダブルクォート)
    content = re.sub(
        r'(fetch|axios\.get|axios\.post|axios\.put|axios\.delete|axios\.patch)\s*\(\s*"(/(?!proxy/))',
        rf'\1("{proxy_prefix}/',
        content,
    )
    # 4. JS テンプレートリテラル: fetch(`/api/...`)
    content = re.sub(
        r"(fetch|axios\.get|axios\.post)\s*\(\s*`(/(?!proxy/))",
        rf"\1(`{proxy_prefix}/",
        content,
    )
    # 5. window.location / location.href = '/...'
    content = re.sub(
        r"(location(?:\.href)?\s*=\s*)'(/(?!proxy/))",
        rf"\1'{proxy_prefix}/",
        content,
    )
    content = re.sub(
        r'(location(?:\.href)?\s*=\s*)"(/(?!proxy/))',
        rf'\1"{proxy_prefix}/',
        content,
    )
    # 6. hardcoded localhost:PORT → /proxy/{key}
    for local_url, proxy_url in _LOCALHOST_REWRITES.items():
        content = content.replace(local_url, proxy_url)
    return content


def _rewrite_location_header(location: str, proxy_prefix: str) -> str:
    """リダイレクト先の Location ヘッダーを書き換える。"""
    if location.startswith("/") and not location.startswith("/proxy/"):
        return f"{proxy_prefix}{location}"
    return location


@router.api_route(
    "/{module_key}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
@router.api_route(
    "/{module_key}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
async def proxy_module(
    request: Request,
    module_key: str,
    path: str = "",
) -> Response:
    """モジュールへのリバースプロキシ（URL書き換え付き）。"""
    port = _MODULE_PORTS.get(module_key)
    if port is None:
        return Response(content=f"Unknown module: {module_key}", status_code=404)

    proxy_prefix = f"/proxy/{module_key}"
    target_url = f"http://localhost:{port}/{path}"
    if request.url.query:
        target_url += f"?{request.url.query}"

    # 転送するヘッダー（Host は書き換え）
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "transfer-encoding")
    }
    headers["host"] = f"localhost:{port}"

    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            upstream = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                content=body,
            )
    except httpx.ConnectError:
        return Response(
            content=f"<h3>モジュール '{module_key}' に接続できません (port {port})</h3>",
            status_code=502,
            media_type="text/html",
        )

    # レスポンスヘッダーを整理
    resp_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in (
            "content-encoding", "transfer-encoding", "content-length",
            "connection", "keep-alive",
        )
    }

    # Location ヘッダーの書き換え（リダイレクト）
    if "location" in upstream.headers:
        resp_headers["location"] = _rewrite_location_header(
            upstream.headers["location"], proxy_prefix
        )

    content_type = upstream.headers.get("content-type", "")
    status_code = upstream.status_code

    # HTML/JS コンテンツの URL 書き換え
    if any(ct in content_type for ct in _REWRITE_CONTENT_TYPES):
        try:
            text = upstream.text
        except Exception:
            text = upstream.content.decode("utf-8", errors="replace")
        rewritten = _rewrite_urls(text, proxy_prefix)
        return Response(
            content=rewritten,
            status_code=status_code,
            headers=resp_headers,
            media_type=content_type.split(";")[0].strip(),
        )

    # バイナリ・JSON はそのまま転送
    return Response(
        content=upstream.content,
        status_code=status_code,
        headers=resp_headers,
        media_type=content_type.split(";")[0].strip() if content_type else None,
    )
