"""
comtrade_fetch.py
────────────────────────────────────────────────────────────────────────────
UN Comtrade API v2 を使った貿易統計（HS別輸出入実績）自動取得サービス。

データソース:
  UN Comtrade API v2  https://comtradeapi.un.org/
  → HS6桁 × 報告国 × 相手国（World）× 年次 で輸出入金額・数量を取得
  → 無料だが **API Key 必要**（https://comtradeapi.un.org で無料登録）
  → 環境変数: COMTRADE_API_KEY

API Key 取得方法:
  1. https://comtradeapi.un.org/  → "API Access" → "Get Your API Key"
  2. UN Comtrade 無料プランで月 500 回リクエスト（研究・教育用）

取得データ構造（trade_stats JSON）:
  {
    "year":       2023,
    "exports": {
      "value_usd":  1234567890,   # USD FOB
      "qty":        12345,
      "qty_unit":   "KG",
      "partner":    "World",
      "rank":       3,             # 世界輸出ランク（対 World）
    },
    "imports": {
      "value_usd":  987654321,
      "qty":        9876,
      "qty_unit":   "KG",
      "partner":    "World",
    },
    "source":     "un_comtrade_v2",
    "fetched_at": "2024-01-01T00:00:00+00:00"
  }

キャッシュ:
  同一 (country_code, hs6, year) のリクエストは 30 日間ファイルキャッシュ。
  cache dir: data/staging/comtrade_cache/

Comtrade M49 Reporter codes:
  JP=392  US=842  CN=156  EU=918  KR=410
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── 設定 ──────────────────────────────────────────────────────────────────────

_CACHE_DIR  = Path(__file__).resolve().parents[4] / "data" / "staging" / "comtrade_cache"
_CACHE_DAYS = 30
_TIMEOUT    = 20.0

_API_BASE   = "https://comtradeapi.un.org/data/v1/get"
_API_KEY    = os.environ.get("COMTRADE_API_KEY", "")

# ISO 2文字 → UN M49 code
_M49: dict[str, str] = {
    "JP": "392",
    "US": "842",
    "CN": "156",
    "EU": "918",   # EU としての集計 (Comtrade では reporter=918 は限定的)
    "KR": "410",
}

# EU 個別国リスト（918 が取れない場合のフォールバック — 代表として DE を使用）
_EU_FALLBACK_M49 = "276"  # Germany

# フロー識別
_FLOW_EXPORT = "X"
_FLOW_IMPORT = "M"


# ── キャッシュ ─────────────────────────────────────────────────────────────────

def _cache_path(country_code: str, hs6: str, year: int) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{country_code}_{hs6}_{year}.json"


def _read_cache(country_code: str, hs6: str, year: int) -> Optional[dict]:
    p = _cache_path(country_code, hs6, year)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(data.get("cached_at", "2000-01-01T00:00:00+00:00"))
        if (datetime.now(timezone.utc) - cached_at).days > _CACHE_DAYS:
            return None
        return data
    except Exception:
        return None


def _write_cache(country_code: str, hs6: str, year: int, payload: dict) -> None:
    p = _cache_path(country_code, hs6, year)
    payload = dict(payload)
    payload["cached_at"] = datetime.now(timezone.utc).isoformat()
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ── UN Comtrade API v2 呼び出し ───────────────────────────────────────────────

async def _call_comtrade(
    reporter_m49: str,
    hs6:          str,
    flow:         str,
    year:         int,
) -> Optional[list[dict]]:
    """Comtrade API v2 を呼び出してレスポンスの data リストを返す。"""

    if not _API_KEY:
        return None

    url = f"{_API_BASE}/C/A/HS"   # typeCode=C, freqCode=A, clCode=HS
    params = {
        "reporterCode":      reporter_m49,
        "flowCode":          flow,
        "period":            str(year),
        "cmdCode":           hs6,
        "partnerCode":       "0",   # 0 = World
        "partner2Code":      "0",
        "motCode":           "0",
        "customsCode":       "C00",
        "maxRecords":        "10",
        "format":            "JSON",
        "countOnly":         "false",
        "includeDesc":       "true",
        "subscription-key":  _API_KEY,
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, params=params)
            if resp.status_code == 401:
                logger.warning("Comtrade API: 401 Unauthorized — check COMTRADE_API_KEY")
                return None
            if resp.status_code == 429:
                logger.warning("Comtrade API: 429 Rate limit exceeded")
                return None
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        logger.warning("Comtrade HTTP %s for %s/%s/%s", e.response.status_code,
                       reporter_m49, hs6, flow)
        return None
    except Exception as e:
        logger.warning("Comtrade API error: %s", e)
        return None

    return data.get("data") or data.get("Data") or []


def _extract_flow(records: list[dict]) -> Optional[dict]:
    """Comtrade レスポンスの data リストから貿易フロー情報を抽出する。"""
    if not records:
        return None

    # 複数レコードがある場合は Partner=World (partnerCode=0) を優先
    world_rec = next(
        (r for r in records if str(r.get("partnerCode", "")) == "0"),
        records[0],
    )

    value = world_rec.get("primaryValue") or world_rec.get("fobvalue") or None
    qty   = world_rec.get("netWgt") or world_rec.get("altQty") or None
    unit  = world_rec.get("altQtyUnitCode") or "KG"

    try:
        value = float(value) if value is not None else None
        qty   = float(qty)   if qty   is not None else None
    except (TypeError, ValueError):
        value = qty = None

    return {
        "value_usd": value,
        "qty":       qty,
        "qty_unit":  unit,
        "partner":   "World",
    }


# ── 公開インターフェース ───────────────────────────────────────────────────────

class TradeStatsResult:
    def __init__(
        self,
        year:    int,
        exports: Optional[dict],
        imports: Optional[dict],
        source:  str,
        cached:  bool = False,
        message: Optional[str] = None,
    ):
        self.year    = year
        self.exports = exports
        self.imports = imports
        self.source  = source
        self.cached  = cached
        self.message = message

    def to_dict(self) -> dict:
        return {
            "year":       self.year,
            "exports":    self.exports,
            "imports":    self.imports,
            "source":     self.source,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    @property
    def has_data(self) -> bool:
        return self.exports is not None or self.imports is not None


async def fetch_trade_stats(
    country_code: str,
    hs6:          str,
    year:         Optional[int] = None,
    force:        bool          = False,
) -> TradeStatsResult:
    """指定国×HS6桁の輸出入統計を取得する。

    Args:
        country_code: "JP" / "US" / "CN" / "EU" / "KR"
        hs6:          6桁 HSコード（例: "850440"）
        year:         取得年（省略時は2年前 — Comtrade は公開が遅延する）
        force:        True でキャッシュを無視して再取得

    Returns:
        TradeStatsResult
    """
    hs6_clean = hs6.strip().replace(".", "").replace("-", "")[:6]
    if not year:
        year = date.today().year - 2   # Comtrade は通常 1〜2年遅延で公開

    # キャッシュ確認
    if not force:
        cached = _read_cache(country_code, hs6_clean, year)
        if cached:
            return TradeStatsResult(
                year    = cached.get("year", year),
                exports = cached.get("exports"),
                imports = cached.get("imports"),
                source  = cached.get("source", "cache"),
                cached  = True,
            )

    # APIキー確認
    if not _API_KEY:
        msg = (
            "COMTRADE_API_KEY が未設定です。"
            " https://comtradeapi.un.org で無料登録後、"
            "環境変数 COMTRADE_API_KEY に設定してください。"
        )
        return TradeStatsResult(
            year=year, exports=None, imports=None,
            source="none", message=msg,
        )

    reporter = _M49.get(country_code)
    if not reporter:
        return TradeStatsResult(
            year=year, exports=None, imports=None,
            source="none", message=f"Unsupported country: {country_code}",
        )

    # EU は918 が reporter として機能しないことがあるため DE にフォールバック
    if country_code == "EU":
        reporter_list = [reporter, _EU_FALLBACK_M49]
    else:
        reporter_list = [reporter]

    exports = imports = None
    used_reporter = reporter

    for r in reporter_list:
        logger.info("Fetching comtrade: reporter=%s hs6=%s year=%d", r, hs6_clean, year)
        exp_records = await _call_comtrade(r, hs6_clean, _FLOW_EXPORT, year)
        imp_records = await _call_comtrade(r, hs6_clean, _FLOW_IMPORT, year)

        exports = _extract_flow(exp_records or [])
        imports = _extract_flow(imp_records or [])

        if exports or imports:
            used_reporter = r
            break

    result = TradeStatsResult(
        year    = year,
        exports = exports,
        imports = imports,
        source  = f"un_comtrade_v2 (reporter={used_reporter})",
        cached  = False,
        message = None if (exports or imports) else f"No data available for {country_code}/{hs6_clean}/{year}",
    )

    # データが取れた場合のみキャッシュ
    if result.has_data:
        _write_cache(country_code, hs6_clean, year, result.to_dict())

    return result
