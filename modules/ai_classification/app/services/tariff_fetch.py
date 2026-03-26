"""
tariff_fetch.py
────────────────────────────────────────────────────────────────────────────
関税率自動取得サービス（WTO API + フォールバック）。

データソース (優先順):
  1. WTO Data Portal API  https://api.wto.org/
     → MFN 実行税率（Applied MFN Tariff）を HS6桁 × 報告国 で取得
     → 無料だが **API Key 必要**（https://api.wto.org で無料登録）
     → 環境変数: WTO_API_KEY

  2. World Bank WITS REST API  https://wits.worldbank.org/
     → フォールバック（こちらも登録推奨）
     → 環境変数: WITS_API_KEY（任意）

API Key 取得方法:
  WTO:   https://api.wto.org/  → "Sign Up" → メール認証 → APIキー発行（無料）
  WITS:  https://wits.worldbank.org/  → 登録不要でも一部エンドポイントは公開

キャッシュ:
  同一 (country_code, hs6, year) のリクエストは 30 日間ファイルキャッシュ。
  cache dir: data/staging/tariff_cache/

WTO Reporter codes（ISO 3166-1 numeric）:
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

_CACHE_DIR  = Path(__file__).resolve().parents[4] / "data" / "staging" / "tariff_cache"
_CACHE_DAYS = 30

_WTO_BASE   = "https://api.wto.org"
_WITS_BASE  = "https://wits.worldbank.org/API/V1"

_TIMEOUT    = 15.0   # seconds

# 環境変数からAPIキーを読み込む
_WTO_API_KEY  = os.environ.get("WTO_API_KEY", "")
_WITS_API_KEY = os.environ.get("WITS_API_KEY", "")

# ISO 2文字 → WTO Reporter コード (ISO 3166-1 numeric)
_WTO_REPORTER: dict[str, str] = {
    "JP": "392",
    "US": "842",
    "CN": "156",
    "EU": "918",
    "KR": "410",
}

# ISO 2文字 → World Bank ISO 3文字 (WITS用)
_WITS_ISO3: dict[str, str] = {
    "JP": "JPN",
    "US": "USA",
    "CN": "CHN",
    "EU": "EUN",
    "KR": "KOR",
}

# WTO API: Applied MFN Tariff 指標コード
_WTO_INDICATOR_MFN = "HS_T_AV_MFN_001_R"   # Simple average applied MFN rate


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
        age_days = (datetime.now(timezone.utc) - cached_at).days
        if age_days > _CACHE_DAYS:
            return None
        return data
    except Exception:
        return None


def _write_cache(country_code: str, hs6: str, year: int, payload: dict) -> None:
    p = _cache_path(country_code, hs6, year)
    payload["cached_at"] = datetime.now(timezone.utc).isoformat()
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ── WTO Data Portal API ───────────────────────────────────────────────────────

async def _fetch_wto(country_code: str, hs6: str, year: int) -> Optional[dict]:
    """WTO Data Portal API から Applied MFN 税率を取得する。

    APIキー要件:
      環境変数 WTO_API_KEY が必要。
      取得方法: https://api.wto.org で無料登録（研究・教育用は無料）。

    エンドポイント:
      GET https://api.wto.org/timeseries/v1/data
          ?i={indicator}&r={reporter}&p=all&pc=HS6&ps={year}&fmt=json
          &Opc=&Lang=1
      Header: Ocp-Apim-Subscription-Key: {WTO_API_KEY}
    """
    if not _WTO_API_KEY:
        logger.info("WTO_API_KEY not set — skipping WTO API fetch. "
                    "Register at https://api.wto.org to enable.")
        return None

    reporter = _WTO_REPORTER.get(country_code)
    if not reporter:
        logger.warning("No WTO reporter code for country: %s", country_code)
        return None

    url = f"{_WTO_BASE}/timeseries/v1/data"
    params = {
        "i":   _WTO_INDICATOR_MFN,
        "r":   reporter,
        "p":   "all",
        "pc":  "HS6",
        "ps":  str(year),
        "fmt": "json",
        "lang": "1",
        "sc":  hs6,        # specific commodity / HS コード フィルタ
    }
    headers = {"Ocp-Apim-Subscription-Key": _WTO_API_KEY}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code == 401:
                logger.warning("WTO API: 401 Unauthorized. Check WTO_API_KEY.")
                return None
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        logger.warning("WTO API HTTP error %s for %s/%s", e.response.status_code, country_code, hs6)
        return None
    except Exception as e:
        logger.warning("WTO API error: %s", e)
        return None

    # WTO timeseries レスポンス: {"Dataset": [{"value": ..., "period": ..., ...}]}
    dataset = data.get("Dataset") or data.get("dataset") or []
    if not dataset:
        logger.info("WTO API: no data for %s/%s/%d", country_code, hs6, year)
        return None

    row = dataset[0]
    rate_raw = row.get("value") or row.get("Value")
    try:
        rate = float(rate_raw) / 100.0 if rate_raw is not None else None
    except (ValueError, TypeError):
        rate = None

    return {
        "tariff_rate":  rate,
        "tariff_type":  "MFN",
        "tariff_notes": f"WTO Applied MFN {year} (simple avg, api.wto.org)",
        "source":       "wto_api",
    }


# ── World Bank WITS API (フォールバック) ─────────────────────────────────────

async def _fetch_wits(country_code: str, hs6: str, year: int) -> Optional[dict]:
    """World Bank WITS から Applied HS Tariff を取得する。

    エンドポイント (SDMX V21):
      GET https://wits.worldbank.org/API/V1/SDMX/V21/datasource/TRN-TARIFF-WTF
          /reporter/{iso3}/partner/WLD/product/{hs6}/indicator/AHS-SMPL-AVRG/
    """
    iso3 = _WITS_ISO3.get(country_code)
    if not iso3:
        return None

    url = (
        f"https://wits.worldbank.org/API/V1/SDMX/V21/datasource/TRN-TARIFF-WTF"
        f"/reporter/{iso3}/partner/WLD/product/{hs6}/indicator/AHS-SMPL-AVRG/"
    )
    headers: dict = {"Accept": "application/json"}
    if _WITS_API_KEY:
        headers["Authorization"] = f"Bearer {_WITS_API_KEY}"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code in (401, 403, 405):
                logger.info("WITS API: %d for %s/%s (registration may be required)",
                            resp.status_code, country_code, hs6)
                return None
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning("WITS API error: %s", e)
        return None

    # SDMX V21 レスポンス解析
    try:
        # {"dataSets": [{"series": {"0:0:0": {"observations": {"0": [value, ...]}}}}]}
        ds_list = data.get("dataSets") or data.get("DataSets") or []
        if not ds_list:
            return None
        series_map = ds_list[0].get("series") or {}
        if not series_map:
            return None
        first_series = next(iter(series_map.values()))
        obs = first_series.get("observations") or {}
        if not obs:
            return None
        last_obs = obs[max(obs.keys(), key=int)]
        rate_raw = last_obs[0] if last_obs else None
        rate = float(rate_raw) / 100.0 if rate_raw is not None else None
    except (KeyError, IndexError, TypeError, ValueError, StopIteration):
        return None

    if rate is None:
        return None

    return {
        "tariff_rate":  rate,
        "tariff_type":  "MFN",
        "tariff_notes": f"WITS AHS {year} (simple avg, wits.worldbank.org)",
        "source":       "wits",
    }


# ── 公開インターフェース ───────────────────────────────────────────────────────

class TariffResult:
    """関税率取得結果。"""

    def __init__(
        self,
        tariff_rate:  Optional[float],
        tariff_type:  Optional[str],
        tariff_notes: Optional[str],
        source:       str,
        cached:       bool = False,
    ):
        self.tariff_rate  = tariff_rate
        self.tariff_type  = tariff_type
        self.tariff_notes = tariff_notes
        self.source       = source
        self.cached       = cached

    def to_dict(self) -> dict:
        return {
            "tariff_rate":  self.tariff_rate,
            "tariff_type":  self.tariff_type,
            "tariff_notes": self.tariff_notes,
            "source":       self.source,
            "cached":       self.cached,
        }


async def fetch_tariff(
    country_code: str,
    hs6:          str,
    year:         Optional[int] = None,
    force:        bool          = False,
) -> TariffResult:
    """指定国×HS6桁の MFN 関税率を取得する。

    Args:
        country_code: "JP" / "US" / "CN" / "EU" / "KR"
        hs6:          6桁 HSコード（例 "850440"）
        year:         取得年（省略時は前年）
        force:        True でキャッシュを無視して再取得

    Returns:
        TariffResult
    """
    hs6_clean = hs6.strip().replace(".", "").replace("-", "")[:6]
    if not year:
        year = date.today().year - 1   # WTO は通常 1〜2年遅れで公開

    # キャッシュ確認
    if not force:
        cached = _read_cache(country_code, hs6_clean, year)
        if cached:
            logger.debug("Cache hit: %s/%s/%d", country_code, hs6_clean, year)
            return TariffResult(
                tariff_rate  = cached.get("tariff_rate"),
                tariff_type  = cached.get("tariff_type"),
                tariff_notes = cached.get("tariff_notes"),
                source       = cached.get("source", "cache"),
                cached       = True,
            )

    # WTO API 試行
    result = await _fetch_wto(country_code, hs6_clean, year)

    # フォールバック: WITS
    if result is None:
        result = await _fetch_wits(country_code, hs6_clean, year)

    if result is None:
        # APIキー未設定の場合は案内メッセージを返す
        if not _WTO_API_KEY:
            notes = (
                "WTO_API_KEY が未設定です。"
                " https://api.wto.org で無料登録後、環境変数 WTO_API_KEY に設定してください。"
                " それまでは手動で関税率を入力してください。"
            )
        else:
            notes = f"API にデータなし (country={country_code}, hs6={hs6_clean}, year={year})"
        return TariffResult(
            tariff_rate  = None,
            tariff_type  = None,
            tariff_notes = notes,
            source       = "none",
        )

    # キャッシュ書き込み
    _write_cache(country_code, hs6_clean, year, dict(result))

    return TariffResult(
        tariff_rate  = result.get("tariff_rate"),
        tariff_type  = result.get("tariff_type"),
        tariff_notes = result.get("tariff_notes"),
        source       = result.get("source", "api"),
        cached       = False,
    )
