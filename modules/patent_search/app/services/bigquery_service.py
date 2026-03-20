"""
BigQuery Service — Google Patents Public Datasets への接続。

修正点:
    - query_job.result() を asyncio.to_thread() でスレッドプールへオフロード
      （元の同期コードが asyncio イベントループをブロックしていた問題を解消）
    - モジュールレベルシングルトン (get_bigquery_service) でリクエストごとの再初期化を回避
    - is_configured() で認証情報の有無を確認（ネットワーク不要）
    - GOOGLE_APPLICATION_CREDENTIALS の相対パスをモジュールルート基点で絶対パスに変換
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Dict, List, Optional

from app.config import settings


class BigQueryService:
    """Service for interacting with Google Patents Public Datasets via BigQuery."""

    def __init__(self):
        self.dataset = settings.bigquery_dataset
        self.table = settings.bigquery_table
        self.client = None
        self._configure_credentials()
        self._initialize_client()

    def _configure_credentials(self) -> None:
        """
        GOOGLE_APPLICATION_CREDENTIALS を解決する。
        相対パスはモジュールルート基点で絶対パスに変換し、
        google-auth が参照する環境変数に設定する。
        """
        creds_path = settings.google_application_credentials
        if not creds_path:
            return

        path = Path(creds_path)
        if not path.is_absolute():
            module_root = Path(__file__).resolve().parents[3]
            path = module_root / path

        if path.exists():
            os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(path))

    def _initialize_client(self) -> None:
        """BigQuery クライアントを初期化する（失敗しても None のまま続行）"""
        try:
            from google.cloud import bigquery
            creds_env = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
            if creds_env and Path(creds_env).exists():
                from google.oauth2 import service_account
                credentials = service_account.Credentials.from_service_account_file(creds_env)
                self.client = bigquery.Client(
                    credentials=credentials,
                    project=settings.gcp_project_id,
                )
            elif settings.gcp_project_id:
                # Application Default Credentials（GCE / Cloud Run 等）
                self.client = bigquery.Client(project=settings.gcp_project_id)
        except Exception as e:
            print(f"[patent_search] BigQuery 初期化スキップ: {e}")
            self.client = None

    # ─────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────

    def is_configured(self) -> bool:
        """クライアントが初期化済みか（ネットワーク不要）"""
        return self.client is not None

    def _build_query(
        self,
        keywords: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        inventor: Optional[str] = None,
        applicant: Optional[str] = None,
        country: Optional[str] = None,
        limit: int = 50,
    ) -> tuple[str, list]:
        """SQL クエリとパラメータを構築する"""
        from google.cloud import bigquery as bq

        query = f"""
        SELECT
            publication_number,
            title_localized[SAFE_OFFSET(0)].text as title,
            abstract_localized[SAFE_OFFSET(0)].text as abstract,
            description_localized[SAFE_OFFSET(0)].text as description,
            filing_date,
            publication_date,
            inventor_harmonized,
            assignee_harmonized,
            country_code
        FROM `{self.dataset}.{self.table}`
        WHERE 1=1
        """
        parameters: list = []

        if keywords:
            query += """
            AND (
                LOWER(title_localized[SAFE_OFFSET(0)].text) LIKE LOWER(@keywords)
                OR LOWER(abstract_localized[SAFE_OFFSET(0)].text) LIKE LOWER(@keywords)
            )
            """
            parameters.append(bq.ScalarQueryParameter("keywords", "STRING", f"%{keywords}%"))

        if date_from:
            query += " AND publication_date >= @date_from"
            parameters.append(bq.ScalarQueryParameter("date_from", "INT64", int(date_from.replace("-", ""))))

        if date_to:
            query += " AND publication_date <= @date_to"
            parameters.append(bq.ScalarQueryParameter("date_to", "INT64", int(date_to.replace("-", ""))))

        if inventor:
            query += """
            AND EXISTS (
                SELECT 1 FROM UNNEST(inventor_harmonized) as inv
                WHERE LOWER(inv.name) LIKE LOWER(@inventor)
            )
            """
            parameters.append(bq.ScalarQueryParameter("inventor", "STRING", f"%{inventor}%"))

        if applicant:
            query += """
            AND EXISTS (
                SELECT 1 FROM UNNEST(assignee_harmonized) as asn
                WHERE LOWER(asn.name) LIKE LOWER(@applicant)
            )
            """
            parameters.append(bq.ScalarQueryParameter("applicant", "STRING", f"%{applicant}%"))

        if country:
            query += " AND country_code = @country"
            parameters.append(bq.ScalarQueryParameter("country", "STRING", country.upper()))

        query += " ORDER BY publication_date DESC"
        query += " LIMIT @limit"
        parameters.append(bq.ScalarQueryParameter("limit", "INT64", min(limit, settings.max_results_limit)))

        return query, parameters

    @staticmethod
    def _yyyymmdd_to_iso(value) -> Optional[str]:
        if not value:
            return None
        s = str(value)
        return f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 else None

    def _sync_execute(self, query: str, job_config) -> list:
        """同期 BigQuery 実行（スレッドプール内で呼ばれる）"""
        query_job = self.client.query(query, job_config=job_config)
        return list(query_job.result())  # ← ブロッキング IO

    # ─────────────────────────────────────────────────
    # Async API
    # ─────────────────────────────────────────────────

    async def search_patents(
        self,
        keywords: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        inventor: Optional[str] = None,
        applicant: Optional[str] = None,
        country: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Dict:
        """
        Google Patents BigQuery を非同期検索する。

        ブロッキング I/O（query_job.result()）を asyncio.to_thread() で
        スレッドプールへオフロードし、イベントループをブロックしない。
        """
        if not self.client:
            raise RuntimeError(
                "BigQuery クライアントが初期化されていません。"
                "GCP_PROJECT_ID と GOOGLE_APPLICATION_CREDENTIALS を確認してください。"
            )

        if limit is None:
            limit = settings.default_results_limit

        query, parameters = self._build_query(
            keywords=keywords,
            date_from=date_from,
            date_to=date_to,
            inventor=inventor,
            applicant=applicant,
            country=country,
            limit=limit,
        )

        from google.cloud import bigquery as bq
        job_config = bq.QueryJobConfig(query_parameters=parameters)

        try:
            # ★ イベントループブロッキング修正: スレッドプールで実行
            rows = await asyncio.to_thread(self._sync_execute, query, job_config)
        except Exception as e:
            raise RuntimeError(f"BigQuery 検索失敗: {e}") from e

        patents = []
        for row in rows:
            inventors = [inv.get("name", "") for inv in (row.inventor_harmonized or []) if inv.get("name")]
            assignees = [asn.get("name", "") for asn in (row.assignee_harmonized or []) if asn.get("name")]
            patents.append({
                "source": "google_patents",
                "publication_number": row.publication_number,
                "title": row.title or "No title available",
                "abstract": row.abstract or "No abstract available",
                "description": row.description or "",
                "filing_date": self._yyyymmdd_to_iso(row.filing_date),
                "publication_date": self._yyyymmdd_to_iso(row.publication_date),
                "inventors": inventors,
                "assignees": assignees,
                "country_code": row.country_code,
                "url": f"https://patents.google.com/patent/{row.publication_number}",
            })

        return {"results": patents, "count": len(patents), "query": keywords}

    async def check_connection(self) -> bool:
        """BigQuery への疎通確認（ネットワークあり）"""
        if not self.client:
            return False
        try:
            from google.cloud import bigquery as bq
            job_config = bq.QueryJobConfig()
            query = f"SELECT 1 FROM `{self.dataset}.{self.table}` LIMIT 1"
            await asyncio.to_thread(self._sync_execute, query, job_config)
            return True
        except Exception as e:
            print(f"[patent_search] BigQuery 接続確認失敗: {e}")
            return False

    def estimate_query_cost(self, query: str) -> Dict:
        """クエリのコスト見積もり（ドライラン、同期）"""
        if not self.client:
            return {"error": "クライアント未初期化"}
        try:
            from google.cloud import bigquery as bq
            job_config = bq.QueryJobConfig(dry_run=True, use_query_cache=False)
            query_job = self.client.query(query, job_config=job_config)
            gb = query_job.total_bytes_processed / (1024 ** 3)
            return {
                "bytes_processed": query_job.total_bytes_processed,
                "gb_processed": round(gb, 4),
                "estimated_cost_usd": round(gb * 0.005, 4),
            }
        except Exception as e:
            return {"error": str(e)}


# ─────────────────────────────────────────────────
# モジュールレベルシングルトン
# ─────────────────────────────────────────────────

_bq_service: Optional[BigQueryService] = None


def get_bigquery_service() -> BigQueryService:
    """シングルトン BigQueryService を返す（リクエストごとに再生成しない）"""
    global _bq_service
    if _bq_service is None:
        _bq_service = BigQueryService()
    return _bq_service
