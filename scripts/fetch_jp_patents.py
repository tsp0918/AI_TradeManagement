"""
fetch_jp_patents.py
──────────────────────────────────────────────────────────────────────────────
JPO IP Data API (https://ip-data.jpo.go.jp) から JP 特許を取得し、
Layer B 統合用 JSON（data/staging/jp_patents_raw.json）に保存するスクリプト。

認証: OAuth2 Resource Owner Password Credentials
  - POST {JPLATPAT_TOKEN_URL} → Bearer token
  - GET  {JPLATPAT_BASE_URL}/api/patent/v1/... で特許検索

取得対象: 輸出管理関連 IPC コード（ECCN に対応する IPC 前方一致リスト）

使い方:
  python scripts/fetch_jp_patents.py [--limit 200] [--ipc H01L,G21C] [--dry-run]

出力:
  data/staging/jp_patents_raw.json   → Layer B 統合スクリプト用中間ファイル

環境変数（.env）:
  JPLATPAT_USERNAME   JPO アカウント ユーザー名
  JPLATPAT_PASSWORD   JPO アカウント パスワード
  JPLATPAT_TOKEN_URL  OAuth2 トークンエンドポイント
  JPLATPAT_BASE_URL   APIベースURL（https://ip-data.jpo.go.jp）
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

# .env 読み込み
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_ROOT    = Path(__file__).resolve().parents[1]
_STAGING = _ROOT / "data" / "staging"
_OUT     = _STAGING / "jp_patents_raw.json"

# 認証設定
_TOKEN_URL  = os.environ.get("JPLATPAT_TOKEN_URL", "https://ip-data.jpo.go.jp/auth/token")
_BASE_URL   = os.environ.get("JPLATPAT_BASE_URL",  "https://ip-data.jpo.go.jp")
_USERNAME   = os.environ.get("JPLATPAT_USERNAME", "")
_PASSWORD   = os.environ.get("JPLATPAT_PASSWORD", "")

# 輸出管理関連 IPC コード（優先度高い順）
# ECCN → IPC マッピングの逆引きで主要なもの
_TARGET_IPC_PREFIXES = [
    # 半導体・電子部品 (3A001/3B001/3C001)
    "H01L",  # 半導体デバイス
    "H03F",  # 増幅器（マイクロ波）
    # 暗号・通信 (5A001/5A002)
    "H04L",  # デジタル情報伝送
    "H04K",  # 秘密通信
    # センサー・光学 (6A001/6A002)
    "G01S",  # 位置・速度センサー（ソナー・レーダー）
    "G01J",  # 光測定（赤外線）
    # 航法・慣性 (7A001/7A002)
    "G01C",  # 測定・ナビゲーション
    "G01P",  # 速度・加速度計測
    # 核・放射線 (0C001)
    "G21C",  # 原子炉
    "G21F",  # 放射線遮蔽
    # 先進材料・複合材 (1C010)
    "C04B",  # セラミックス・複合材
    "D01F",  # 炭素繊維
    # 推進剤・爆発物 (1C111)
    "C06B",  # 爆発物・推進剤
    # 工作機械 (2B001)
    "B23B",  # 旋盤
    "B24B",  # 研削
    # 航空宇宙 (9A001/9A004/9A610)
    "B64C",  # 航空機
    "B64G",  # 宇宙機
    "F02K",  # ジェット推進
    # 海洋 (8A001)
    "B63G",  # 潜水艦
    # コンピュータ (4A003)
    "G06F",  # デジタル計算
    # 高周波・レーダー
    "H01Q",  # アンテナ
]

_LIMIT_PER_IPC = 50  # IPC コードあたり最大取得件数（デフォルト）


def _get_token() -> str:
    """JPO OAuth2 から Bearer トークンを取得する。"""
    if not _USERNAME or not _PASSWORD:
        raise ValueError(
            "JPLATPAT_USERNAME / JPLATPAT_PASSWORD が .env に未設定です。"
        )
    resp = httpx.post(
        _TOKEN_URL,
        data={
            "grant_type": "password",
            "username":   _USERNAME,
            "password":   _PASSWORD,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("access_token") or data.get("token")
    if not token:
        raise ValueError(f"トークン取得失敗: {data}")
    logger.info("JPO トークン取得成功 (expires_in=%s)", data.get("expires_in", "?"))
    return token


def _search_by_ipc(token: str, ipc_prefix: str, limit: int) -> list[dict]:
    """IPC コードで JP 特許を検索する。

    JPO IP Data API のエンドポイントを試みる。
    フォールバックとして J-PlatPat simpleSearch も使用。
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    # JPO IP Data API — IPC 分類コード検索
    # 主要エンドポイント候補
    endpoints = [
        f"{_BASE_URL}/api/patent/v1/technical/search",
        f"{_BASE_URL}/api/patent/v1/simple/search",
    ]
    params_variants = [
        {"ipcCode": ipc_prefix, "rows": limit, "type": "P"},
        {"ipc": ipc_prefix, "limit": limit},
        {"query": f"IPC={ipc_prefix}", "rows": limit},
    ]

    for endpoint in endpoints:
        for params in params_variants:
            try:
                resp = httpx.get(endpoint, params=params, headers=headers, timeout=30.0)
                if resp.status_code == 200:
                    data = resp.json()
                    # レスポンス構造は API バージョンにより異なる
                    results = (
                        data.get("result", data.get("results",
                        data.get("patents", data.get("items", []))))
                    )
                    if results:
                        logger.info("  %s → %d件 (endpoint=%s)",
                                    ipc_prefix, len(results),
                                    endpoint.split("/")[-1])
                        return results
                elif resp.status_code in (400, 404):
                    continue  # このエンドポイントは対応していない
                elif resp.status_code == 401:
                    raise ValueError("認証エラー。トークンを確認してください。")
            except (httpx.HTTPStatusError, httpx.ConnectError):
                continue

    logger.warning("  %s → 0件 (全エンドポイント応答なし)", ipc_prefix)
    return []


def _normalize_patent(raw: dict, ipc_prefix: str) -> dict | None:
    """API レスポンスの特許データを Layer B 統合形式に正規化する。"""
    # フィールド候補（APIバージョンにより異なる）
    pub_no = (
        raw.get("publicationNumber") or raw.get("publication_number") or
        raw.get("appNumber") or raw.get("applicationNumber") or ""
    )
    if not pub_no:
        return None

    title = (
        raw.get("title") or raw.get("inventionTitle") or
        raw.get("titleJa") or raw.get("title_ja") or
        raw.get("titleEn") or ""
    )
    abstract = (
        raw.get("abstract") or raw.get("abstractJa") or
        raw.get("summary") or raw.get("claims") or ""
    )
    ipc_codes = (
        raw.get("ipcCode") or raw.get("ipc_code") or
        raw.get("ipc") or ipc_prefix
    )
    if isinstance(ipc_codes, list):
        ipc_codes = ",".join(ipc_codes)

    filing_date = (
        raw.get("filingDate") or raw.get("filing_date") or
        raw.get("applicationDate") or ""
    )

    # JP 特許番号の正規化
    if pub_no and not pub_no.startswith("JP"):
        pub_no = f"JP-{pub_no}"

    return {
        "source_type":        "patent",
        "source_name":        "jpo_ip_data",
        "publication_number": pub_no,
        "country_code":       "JP",
        "ipc_codes":          str(ipc_codes)[:200],
        "title":              str(title)[:300],
        "abstract":           str(abstract)[:600],
        "filing_date":        str(filing_date),
        "fefta_items":        [],
        "has_fefta_mapping":  False,
        "_ipc_prefix_searched": ipc_prefix,
    }


def fetch(ipc_list: list[str], limit_per_ipc: int, dry_run: bool) -> None:
    if not _USERNAME or not _PASSWORD:
        logger.error(
            "JPLATPAT_USERNAME / JPLATPAT_PASSWORD が未設定です。"
            ".env を確認してください。"
        )
        sys.exit(1)

    logger.info("JPO API 認証中 (%s)", _TOKEN_URL)
    try:
        token = _get_token()
    except Exception as e:
        logger.error("トークン取得失敗: %s", e)
        sys.exit(1)

    if dry_run:
        logger.info("[dry-run] 認証成功。実際の検索はスキップします。")
        return

    all_patents: list[dict] = []
    seen_pub_nos: set[str] = set()
    total_raw = 0

    for idx, ipc in enumerate(ipc_list, 1):
        logger.info("[%d/%d] IPC=%s", idx, len(ipc_list), ipc)
        raw_list = _search_by_ipc(token, ipc, limit_per_ipc)
        total_raw += len(raw_list)

        for raw in raw_list:
            normalized = _normalize_patent(raw, ipc)
            if normalized and normalized["publication_number"] not in seen_pub_nos:
                seen_pub_nos.add(normalized["publication_number"])
                all_patents.append(normalized)

        # API レート制限を避けるため少し待機
        time.sleep(0.5)

    logger.info("取得完了: %d件（重複除去後）/ %d件（生データ）", len(all_patents), total_raw)

    if not all_patents:
        logger.warning("特許データが取得できませんでした。")
        logger.warning("API エンドポイントの構造を確認してください。")
        logger.warning("  TOKEN_URL: %s", _TOKEN_URL)
        logger.warning("  BASE_URL:  %s", _BASE_URL)
        # 空でも保存して後で確認できるようにする
    else:
        # IPC別カウント
        ipc_counts: dict[str, int] = {}
        for p in all_patents:
            pfx = p.get("_ipc_prefix_searched", "?")
            ipc_counts[pfx] = ipc_counts.get(pfx, 0) + 1
        logger.info("IPC別件数: %s", sorted(ipc_counts.items(), key=lambda x: -x[1])[:10])

    _STAGING.mkdir(parents=True, exist_ok=True)
    out = {
        "total": len(all_patents),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "jpo_ip_data",
        "ipc_list": ipc_list,
        "patents": all_patents,
    }
    _OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("保存: %s (%d件)", _OUT, len(all_patents))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="JPO IP Data API から JP 特許を取得して Layer B 統合用 JSON に保存"
    )
    parser.add_argument(
        "--limit", type=int, default=_LIMIT_PER_IPC,
        help=f"IPC コードあたり最大取得件数 (default: {_LIMIT_PER_IPC})"
    )
    parser.add_argument(
        "--ipc", type=str, default="",
        help="対象 IPC コードをカンマ区切りで指定 (default: 全 {len(_TARGET_IPC_PREFIXES)} コード)"
    )
    parser.add_argument("--dry-run", action="store_true", help="認証のみテスト")
    args = parser.parse_args()

    ipc_list = (
        [x.strip() for x in args.ipc.split(",") if x.strip()]
        if args.ipc else _TARGET_IPC_PREFIXES
    )
    fetch(ipc_list=ipc_list, limit_per_ipc=args.limit, dry_run=args.dry_run)
