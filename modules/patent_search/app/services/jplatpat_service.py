"""J-Platpat 特許情報取得 API クライアント。

認証（アクセストークン取得・リフレッシュ）と
各 API エンドポイントの呼び出しをまとめる。

アクセス上限（デフォルト）:
  app_progress     : 400 回/日
  registration_info: 200 回/日
  cite_doc_info    : 50  回/日
  jpp_fixed_address: 200 回/日
レート制限: 10 リクエスト/分
"""
from __future__ import annotations

import asyncio
import json
import re as _re
import time
from typing import Any, Optional

import httpx

from app.config import settings


# ── トークンキャッシュ（プロセス内シングルトン） ──────────────────────────
_token_cache: dict = {
    "access_token": None,
    "refresh_token": None,
    "expires_at": 0.0,      # UNIX timestamp
    "refresh_expires_at": 0.0,
}
_TOKEN_MARGIN_SEC = 60   # 有効期限の60秒前にリフレッシュ
_RATE_LIMIT_INTERVAL = 6.1  # 10req/min = 1req/6秒（少し余裕を持つ）
_last_request_time: float = 0.0


class JplatpatAuthError(Exception):
    pass


class JplatpatAPIError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


async def _acquire_token() -> str:
    """ID/Password でアクセストークンを新規取得する。"""
    if not settings.jplatpat_username or not settings.jplatpat_password:
        raise JplatpatAuthError(
            "J-Platpat の認証情報が未設定です。"
            " .env に JPLATPAT_USERNAME / JPLATPAT_PASSWORD を設定してください。"
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            settings.jplatpat_token_url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "password",
                "username": settings.jplatpat_username,
                "password": settings.jplatpat_password,
            },
        )
        if resp.status_code != 200:
            raise JplatpatAuthError(
                f"トークン取得に失敗しました (HTTP {resp.status_code}): {resp.text[:200]}"
            )
        data = resp.json()

    now = time.time()
    _token_cache["access_token"] = data["access_token"]
    _token_cache["refresh_token"] = data.get("refresh_token")
    _token_cache["expires_at"] = now + data.get("expires_in", 3600)
    _token_cache["refresh_expires_at"] = now + data.get("refresh_expires_in", 28800)
    return data["access_token"]


async def _refresh_token() -> str:
    """リフレッシュトークンでアクセストークンを再取得する。"""
    refresh = _token_cache.get("refresh_token")
    if not refresh:
        return await _acquire_token()

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            settings.jplatpat_token_url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "refresh_token", "refresh_token": refresh},
        )
        if resp.status_code != 200:
            # リフレッシュ失敗 → ID/Pass で再取得
            return await _acquire_token()
        data = resp.json()

    now = time.time()
    _token_cache["access_token"] = data["access_token"]
    _token_cache["refresh_token"] = data.get("refresh_token", refresh)
    _token_cache["expires_at"] = now + data.get("expires_in", 3600)
    _token_cache["refresh_expires_at"] = now + data.get("refresh_expires_in", 28800)
    return data["access_token"]


async def _get_valid_token() -> str:
    """有効なアクセストークンを返す（自動リフレッシュ込み）。"""
    now = time.time()
    expires_at = _token_cache["expires_at"]
    refresh_expires_at = _token_cache["refresh_expires_at"]

    if not _token_cache["access_token"]:
        return await _acquire_token()

    if now >= expires_at - _TOKEN_MARGIN_SEC:
        # アクセストークン期限切れ間近
        if now < refresh_expires_at - _TOKEN_MARGIN_SEC:
            return await _refresh_token()
        else:
            # リフレッシュトークンも期限切れ → 再認証
            return await _acquire_token()

    return _token_cache["access_token"]


async def _rate_limited_get(url: str) -> dict:
    """レート制限（10req/min）を守りながら GET リクエストを実行する。"""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _RATE_LIMIT_INTERVAL:
        await asyncio.sleep(_RATE_LIMIT_INTERVAL - elapsed)

    token = await _get_valid_token()
    _last_request_time = time.time()

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
        )

    if resp.status_code == 401:
        # トークンが無効 → 強制再取得してリトライ
        _token_cache["access_token"] = None
        token = await _acquire_token()
        _last_request_time = time.time()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
            )

    if resp.status_code != 200:
        raise JplatpatAPIError(
            f"API エラー (HTTP {resp.status_code}): {resp.text[:300]}",
            status_code=resp.status_code,
        )

    return resp.json()


def _normalize_app_number(raw: str) -> str:
    """
    J-PlatPat CSV から読んだ出願番号を API 用形式（10桁数字）に正規化する。

    例:
      "特願2020-008423"  → "2020008423"
      "2020-008423"      → "2020008423"
      "2020008423"       → "2020008423"
    """
    s = raw.strip()
    # 「特願」「特開」等の漢字プレフィックスを除去
    for prefix in ("特願", "特開", "特許", "実願", "実開"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    # ハイフン除去
    s = s.replace("-", "").replace("−", "").replace("‐", "")
    # スラッシュ除去（PCT形式対応: PCT/JP2020/008423 → 別処理）
    s = s.replace("/", "")
    return s


# ─────────────────────────────────────────────────────────────────────
#  公開 API
# ─────────────────────────────────────────────────────────────────────

async def fetch_app_progress(app_number_raw: str) -> dict:
    """
    特許経過情報を取得する（API #1: app_progress）。
    IPC分類、FI/Fターム、出願人、発明者、各種日付を含む。

    Args:
        app_number_raw: 出願番号（J-PlatPat CSV の表記そのまま可）
    Returns:
        API レスポンスの `data` フィールド dict
    """
    num = _normalize_app_number(app_number_raw)
    url = f"{settings.jplatpat_base_url}/api/patent/v1/app_progress/{num}"
    resp = await _rate_limited_get(url)
    _check_status(resp, url)
    return resp.get("result", {}).get("data", {})


async def fetch_registration_info(app_number_raw: str) -> dict:
    """
    特許登録情報を取得する（API #12: registration_info）。
    登録番号、登録日、登録 IPC を含む。
    """
    num = _normalize_app_number(app_number_raw)
    url = f"{settings.jplatpat_base_url}/api/patent/v1/registration_info/{num}"
    resp = await _rate_limited_get(url)
    _check_status(resp, url)
    return resp.get("result", {}).get("data", {})


async def fetch_cite_doc_info(app_number_raw: str) -> dict:
    """
    引用文献情報を取得する（API #11: cite_doc_info）。
    拒絶理由で引用された先行文献リストを含む。
    """
    num = _normalize_app_number(app_number_raw)
    url = f"{settings.jplatpat_base_url}/api/patent/v1/cite_doc_info/{num}"
    resp = await _rate_limited_get(url)
    _check_status(resp, url)
    return resp.get("result", {}).get("data", {})


async def fetch_jpp_fixed_address(app_number_raw: str) -> str:
    """
    J-PlatPat の固定アドレス URL を取得する（API #13: jpp_fixed_address）。
    """
    num = _normalize_app_number(app_number_raw)
    url = f"{settings.jplatpat_base_url}/api/patent/v1/jpp_fixed_address/{num}"
    resp = await _rate_limited_get(url)
    _check_status(resp, url)
    data = resp.get("result", {}).get("data", {})
    # 固定アドレスは複数の候補が返ることがある
    if isinstance(data, list) and data:
        return data[0].get("jppFixedAddress", "")
    if isinstance(data, dict):
        return data.get("jppFixedAddress", "")
    return ""


# ─────────────────────────────────────────────────────────────────────
#  将来拡張: 申請書類（明細書・請求項 XML）取得
# ─────────────────────────────────────────────────────────────────────

async def fetch_app_doc_xml(app_number_raw: str) -> bytes:
    """
    特許申請書類（明細書・請求項 XML）を ZIP バイト列で取得する（API #8）。
    Phase 2 で使用予定。上限 100 回/日。
    """
    num = _normalize_app_number(app_number_raw)
    url = f"{settings.jplatpat_base_url}/api/patent/v1/app_doc/{num}"

    token = await _get_valid_token()
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})

    if resp.status_code != 200:
        raise JplatpatAPIError(
            f"申請書類取得エラー (HTTP {resp.status_code})",
            status_code=resp.status_code,
        )
    return resp.content  # ZIP バイト列


# ─────────────────────────────────────────────────────────────────────
#  ヘルパー
# ─────────────────────────────────────────────────────────────────────

def _check_status(resp: dict, url: str) -> None:
    """API レスポンスのステータスコードを確認する。"""
    result = resp.get("result", {})
    status_code = result.get("statusCode", "")
    if status_code and str(status_code) != "100":
        error_msg = result.get("errorMessage", "")
        raise JplatpatAPIError(
            f"API ステータスエラー ({status_code}): {error_msg} [URL: {url}]"
        )


def parse_progress_to_fields(data: dict) -> dict:
    """
    app_progress の `data` dict から表示・保存に必要なフィールドを抽出する。

    実際のレスポンスフィールド名:
      - inventionTitle          … 発明の名称
      - applicantAttorney[]     … 出願人(class=1) / 代理人(class=2)
      - filingDate              … 出願日 (YYYYMMDD)
      - publicationNumber       … 公開番号
      - publicationDate         … 公開日 (YYYYMMDD)
      - registrationNumber      … 登録番号
      - registrationDate        … 登録日 (YYYYMMDD)
      - bibliographyInformation … 書類一覧（明細書・請求項など）

    Note: IPC / FI / Fターム は JSON レスポンスに含まれません。
          特許文書 XML（Phase 2）で取得可能です。
    """
    if not data:
        return _empty_fields()

    # 発明の名称
    title = data.get("inventionTitle") or ""

    # 出願人（applicantAttorneyClass == "1"）と代理人（"2"）を分離
    applicants: list[str] = []
    attorneys: list[str] = []
    for ap in data.get("applicantAttorney") or []:
        name = (ap.get("name") or "").strip()
        if not name:
            continue
        cls = str(ap.get("applicantAttorneyClass") or "")
        if cls == "1":
            applicants.append(name)
        elif cls == "2":
            attorneys.append(name)

    # 日付
    filing_date        = _parse_date(data.get("filingDate"))
    publication_number = data.get("publicationNumber") or data.get("ADPublicationNumber") or ""
    publication_date   = _parse_date(data.get("publicationDate"))
    registration_number = data.get("registrationNumber") or ""
    registration_date  = _parse_date(data.get("registrationDate"))

    # 書類一覧から利用可能な書類種別を収集
    available_docs: list[str] = []
    for bib in data.get("bibliographyInformation") or []:
        for doc in bib.get("documentList") or []:
            if doc.get("availabilityFlag") == "1":
                desc = doc.get("documentDescription") or doc.get("documentCode") or ""
                if desc and desc not in available_docs:
                    available_docs.append(desc)

    return {
        "title":               title,
        "applicants":          applicants,
        "attorneys":           attorneys,
        "inventors":           [],        # app_progress には発明者リストなし
        "filing_date":         filing_date,
        "publication_number":  publication_number,
        "publication_date":    publication_date,
        "registration_number": registration_number,
        "registration_date":   registration_date,
        "ipc_codes":           [],        # JSON API では取得不可（Phase 2: XML）
        "fi_codes":            [],        # JSON API では取得不可（Phase 2: XML）
        "f_terms":             [],        # JSON API では取得不可（Phase 2: XML）
        "app_status":          "",
        "available_docs":      available_docs,
        "raw": data,
    }


def parse_registration_to_fields(data: dict) -> dict:
    """
    registration_info の `data` から必要フィールドを抽出する。

    実際のフィールド:
      - registrationNumber … 登録番号
      - registrationDate   … 登録日
      - rightPersonInformation[] … 権利者（name フィールド）
      - inventionTitle     … 発明の名称
      - numberOfClaims     … 請求項数
    """
    if not data:
        return {}

    right_persons = [
        p.get("rightPersonName", "")
        for p in (data.get("rightPersonInformation") or [])
        if p.get("rightPersonName")
    ]

    return {
        "registration_number": data.get("registrationNumber") or "",
        "registration_date":   _parse_date(data.get("registrationDate")),
        "right_persons":       right_persons,
        "number_of_claims":    data.get("numberOfClaims") or "",
        "expire_date":         _parse_date(data.get("expireDate")),
        "registration_ipc":    [],   # registration_info にも IPC は含まれない
        "raw_registration":    data,
    }


def _empty_fields() -> dict:
    return {
        "title": "", "applicants": [], "inventors": [],
        "filing_date": None, "publication_number": "",
        "publication_date": None, "ipc_codes": [], "fi_codes": [],
        "f_terms": [], "app_status": "", "raw": {},
    }


def _parse_date(val: Any) -> Optional[str]:
    """'20200115' → '2020-01-15'、None/空文字は None を返す。"""
    if not val:
        return None
    s = str(val).strip().replace("-", "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return str(val)


# ─────────────────────────────────────────────────────────────────────
#  Google Patents フォールバック取得（IPC・要約・請求項・明細書）
# ─────────────────────────────────────────────────────────────────────

_GOOGLE_PATENTS_RATE = 3.0   # 秒 / リクエスト
_last_google_request: float = 0.0


async def fetch_from_google_patents(
    pub_number: str,
    reg_number: str = "",
    ad_pub_number: str = "",
) -> dict:
    """
    Google Patents から IPC・要約・請求項・明細書テキストを取得する。

    J-PlatPat 書類 API (#8) の代替。権限不要、100% 無料。

    試行順: ADPublicationNumber (JP20XXXXXXA) → publicationNumber (JP4XXXXXXXB2)

    Returns:
        {
            "ipc_codes": [...],
            "abstract": "...",
            "claims": "...",
            "description": "...",
            "source_url": "...",
            "google_doc_id": "...",
        }
    """
    global _last_google_request
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ja,en;q=0.9",
    }

    # 試行する Google Patents doc ID のリスト
    doc_ids: list[str] = []
    if ad_pub_number and ad_pub_number.isdigit():
        # ADPublicationNumber: "2019041059" → "JP2019041059A"
        doc_ids.append(f"JP{ad_pub_number}A")
    if reg_number and reg_number.isdigit():
        doc_ids.append(f"JP{reg_number}B2")
    if pub_number and pub_number.isdigit():
        doc_ids.append(f"JP{pub_number}A")

    for doc_id in doc_ids:
        # レート制限
        elapsed = time.time() - _last_google_request
        if elapsed < _GOOGLE_PATENTS_RATE:
            await asyncio.sleep(_GOOGLE_PATENTS_RATE - elapsed)
        _last_google_request = time.time()

        url = f"https://patents.google.com/patent/{doc_id}"
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                continue
            html = resp.text

            # IPC コード: itemprop="Code" で信頼性高い抽出
            ipc_raw = _re.findall(
                r'itemprop=["\']Code["\'][^>]*>\s*([A-H]\d{2}[A-Z]\s*\d+/\d+)',
                html,
            )
            if not ipc_raw:
                # フォールバック: テキスト全体から正規表現
                ipc_raw = _re.findall(r'\b([A-H]\d{2}[A-Z]\s*\d+/\d+)', html)

            ipc_codes = sorted(set(c.replace(" ", "") for c in ipc_raw))[:30]

            # 要約（日本語）
            abstract = ""
            abs_m = _re.search(
                r'class=["\']abstract["\'][^>]*>(.*?)</(?:div|section)>',
                html, _re.DOTALL,
            )
            if abs_m:
                abstract = _re.sub(r'<[^>]+>', '', abs_m.group(1))
                abstract = _re.sub(r'\s+', ' ', abstract).strip()

            # 請求項
            claims = ""
            claims_m = _re.search(
                r'class=["\']claims["\'][^>]*>(.*?)</section>',
                html, _re.DOTALL,
            )
            if claims_m:
                claims_raw_html = _re.sub(r'<[^>]+>', ' ', claims_m.group(1))
                claims = _re.sub(r'\s+', ' ', claims_raw_html).strip()

            # 明細書（先頭 8000 文字）
            description = ""
            desc_m = _re.search(
                r'class=["\']description["\'][^>]*>(.*?)</section>',
                html, _re.DOTALL,
            )
            if desc_m:
                desc_raw_html = _re.sub(r'<[^>]+>', ' ', desc_m.group(1))
                description = _re.sub(r'\s+', ' ', desc_raw_html).strip()[:8000]

            return {
                "ipc_codes":    ipc_codes,
                "abstract":     abstract,
                "claims":       claims,
                "description":  description,
                "source_url":   url,
                "google_doc_id": doc_id,
            }
        except Exception:
            continue

    return {
        "ipc_codes": [], "abstract": "", "claims": "",
        "description": "", "source_url": "", "google_doc_id": "",
    }
