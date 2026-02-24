#!/usr/bin/env python3
"""
sanctions_screening_dataset.json を screening モジュールの Watchlist テーブルに
直接インポートし、FAISS インデックスを再構築するスクリプト。

使い方 (プロジェクトルートから実行):
  BASE=/path/to/AI_TradeManagement

  # Step 1: DB インサート
  PYTHONPATH=$BASE/platform-core:$BASE/modules/screening \\
    $BASE/.venv/bin/python $BASE/scripts/import_sanctions.py --db-only

  # Step 2: FAISS インデックス構築 (PyTorch が必要)
  PYTHONPATH=$BASE/platform-core:$BASE/modules/screening \\
    OMP_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false \\
    $BASE/.venv/bin/python $BASE/scripts/import_sanctions.py --faiss-only

  # 両ステップを一括実行する場合も同じ環境変数を付ける:
  PYTHONPATH=$BASE/platform-core:$BASE/modules/screening \\
    OMP_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false \\
    $BASE/.venv/bin/python $BASE/scripts/import_sanctions.py

環境変数:
  SCREENING_DATABASE_URL : 接続先 DB
      (default: postgresql+asyncpg://platform_user:platform_pass@localhost:5432/platform_db)
  DATASET_PATH           : JSON ファイルパス
      (default: <project_root>/data/sanctions_screening_dataset.json)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── 国名 → ISO 2 文字コード マッピング ────────────────────────────────────
COUNTRY_MAP: dict[str, str] = {
    "Afghanistan": "AF", "Albania": "AL", "Algeria": "DZ", "Angola": "AO",
    "Argentina": "AR", "Armenia": "AM", "Austria": "AT", "Azerbaijan": "AZ",
    "Bahrain": "BH", "Bangladesh": "BD", "Belarus": "BY", "Belgium": "BE",
    "Belize": "BZ", "Bolivia": "BO", "Bosnia and Herzegovina": "BA",
    "Brazil": "BR", "Bulgaria": "BG", "Cambodia": "KH", "Cameroon": "CM",
    "Canada": "CA", "Central African Republic": "CF", "Chad": "TD",
    "Chile": "CL", "China": "CN", "Colombia": "CO", "Congo": "CG",
    "Costa Rica": "CR", "Croatia": "HR", "Cuba": "CU", "Cyprus": "CY",
    "Czech Republic": "CZ", "Denmark": "DK", "Dominican Republic": "DO",
    "Ecuador": "EC", "Egypt": "EG", "El Salvador": "SV", "Eritrea": "ER",
    "Ethiopia": "ET", "Finland": "FI", "France": "FR", "Georgia": "GE",
    "Germany": "DE", "Ghana": "GH", "Greece": "GR", "Guatemala": "GT",
    "Guinea": "GN", "Haiti": "HT", "Honduras": "HN", "Hong Kong": "HK",
    "Hungary": "HU", "India": "IN", "Indonesia": "ID", "Iran": "IR",
    "Iraq": "IQ", "Ireland": "IE", "Israel": "IL", "Italy": "IT",
    "Jamaica": "JM", "Japan": "JP", "Jordan": "JO", "Kazakhstan": "KZ",
    "Kenya": "KE", "North Korea": "KP", "South Korea": "KR",
    "Kosovo": "XK", "Kuwait": "KW", "Kyrgyzstan": "KG", "Laos": "LA",
    "Latvia": "LV", "Lebanon": "LB", "Libya": "LY", "Lithuania": "LT",
    "Luxembourg": "LU", "Malaysia": "MY", "Mali": "ML", "Malta": "MT",
    "Mexico": "MX", "Moldova": "MD", "Mongolia": "MN", "Morocco": "MA",
    "Mozambique": "MZ", "Myanmar": "MM", "Nepal": "NP", "Netherlands": "NL",
    "Nicaragua": "NI", "Nigeria": "NG", "Norway": "NO", "Oman": "OM",
    "Pakistan": "PK", "Palestine": "PS", "Panama": "PA", "Paraguay": "PY",
    "Peru": "PE", "Philippines": "PH", "Poland": "PL", "Portugal": "PT",
    "Qatar": "QA", "Romania": "RO", "Russia": "RU", "Saudi Arabia": "SA",
    "Senegal": "SN", "Serbia": "RS", "Sierra Leone": "SL", "Singapore": "SG",
    "Somalia": "SO", "South Africa": "ZA", "South Sudan": "SS", "Spain": "ES",
    "Sri Lanka": "LK", "Sudan": "SD", "Suriname": "SR", "Sweden": "SE",
    "Switzerland": "CH", "Syria": "SY", "Taiwan": "TW", "Tajikistan": "TJ",
    "Tanzania": "TZ", "Thailand": "TH", "Tunisia": "TN", "Turkey": "TR",
    "Turkmenistan": "TM", "Uganda": "UG", "Ukraine": "UA",
    "United Arab Emirates": "AE", "United Kingdom": "GB",
    "United States": "US", "Uruguay": "UY", "Uzbekistan": "UZ",
    "Venezuela": "VE", "Vietnam": "VN", "Yemen": "YE", "Zimbabwe": "ZW",
}


def _to_country_code(raw: str) -> str | None:
    """国名または既存コードを最大 10 文字の値に変換する。"""
    if not raw:
        return None
    raw = raw.strip()
    if len(raw) <= 3:
        return raw.upper()
    return COUNTRY_MAP.get(raw, raw[:10])


def _build_row(e: dict) -> dict:
    """JSON エントリ → WatchlistImportRow 相当の dict。"""
    address_parts = [p for p in [e.get("address", ""), e.get("city", "")] if p]
    address = ", ".join(address_parts) or None
    programs = e.get("programs") or []
    reason = ", ".join(programs) if programs else None
    return {
        "list_source": e.get("sanctions_list") or "UNKNOWN",
        "entity_name": e.get("name") or "",
        "aliases": e.get("aliases") or None,
        "address": address,
        "country": _to_country_code(e.get("country", "")),
        "source_id": e.get("entity_id"),
        "reason": reason,
        "risk_level": "high",
        "extra": {
            "entity_type": e.get("entity_type"),
            "listing_date": e.get("listing_date"),
            "city": e.get("city"),
        },
    }


async def _insert_all(rows: list[dict]) -> None:
    """Watchlist テーブルに一括インサート。"""
    from app.db.session import AsyncSessionLocal
    from app.models.screening import Watchlist

    BATCH = 500
    async with AsyncSessionLocal() as db:
        for i in range(0, len(rows), BATCH):
            chunk = rows[i: i + BATCH]
            objs = [
                Watchlist(
                    id=uuid.uuid4(),
                    list_source=r["list_source"],
                    entity_name=r["entity_name"],
                    aliases=r["aliases"],
                    address=r["address"],
                    country=r["country"],
                    source_id=r["source_id"],
                    reason=r["reason"],
                    risk_level=r["risk_level"],
                    extra=r["extra"],
                    is_active=True,
                )
                for r in chunk
                if r["entity_name"]
            ]
            db.add_all(objs)
            await db.flush()
            logger.info("  inserted %d / %d", min(i + BATCH, len(rows)), len(rows))
        await db.commit()


async def _load_entity_tuples() -> list[tuple]:
    """DB から (id, entity_name, aliases, address) の tuple リストを返す。"""
    from app.db.session import AsyncSessionLocal
    from app.models.screening import Watchlist
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        r = await db.execute(
            select(Watchlist.id, Watchlist.entity_name, Watchlist.aliases, Watchlist.address)
            .where(Watchlist.is_active == True)  # noqa: E712
        )
        return r.all()


class _Entity:
    """FAISS ビルド用の軽量エンティティオブジェクト。"""
    __slots__ = ("id", "entity_name", "aliases", "address")

    def __init__(self, eid: str, entity_name: str, aliases, address):
        self.id = uuid.UUID(eid)
        self.entity_name = entity_name
        self.aliases = aliases
        self.address = address


def _build_faiss(entity_tuples: list[tuple]) -> None:
    """FAISS インデックスを構築して保存する。asyncpg と分離して呼ぶこと。"""
    from app.services import faiss_service
    entities = [_Entity(str(t.id), t.entity_name, t.aliases, t.address) for t in entity_tuples]
    logger.info("FAISS インデックスを構築中 (%d entities)…", len(entities))
    faiss_service.rebuild(entities)
    logger.info("FAISS インデックス構築完了: ntotal=%d", faiss_service.ntotal())


def main() -> None:
    dataset_path = Path(
        os.environ.get(
            "DATASET_PATH",
            Path(__file__).resolve().parents[1] / "data" / "sanctions_screening_dataset.json",
        )
    )

    do_db    = "--faiss-only" not in sys.argv
    do_faiss = "--db-only"    not in sys.argv

    # ── Step 1: DB インサート ──────────────────────────────────────────────
    if do_db:
        if not dataset_path.exists():
            logger.error("データセットが見つかりません: %s", dataset_path)
            sys.exit(1)
        logger.info("データセットを読み込み中: %s", dataset_path)
        with open(dataset_path, encoding="utf-8") as f:
            data = json.load(f)
        raw_entries = data.get("entities", [])
        logger.info("エントリ数: %d", len(raw_entries))
        rows = [_build_row(e) for e in raw_entries]
        valid_rows = [r for r in rows if r["entity_name"]]
        logger.info("有効エントリ数: %d", len(valid_rows))
        logger.info("Watchlist テーブルへインサート開始…")
        asyncio.run(_insert_all(valid_rows))
        logger.info("✅ DB インサート完了")

    # ── Step 2: FAISS 再構築 ────────────────────────────────────────────────
    if do_faiss:
        logger.info("DB からエンティティを読み込み中…")
        tuples = asyncio.run(_load_entity_tuples())
        logger.info("読み込み完了: %d 件", len(tuples))
        _build_faiss(tuples)
        logger.info("✅ FAISS インデックス構築完了")

    logger.info("🏁 すべての処理が完了しました。")


if __name__ == "__main__":
    main()
