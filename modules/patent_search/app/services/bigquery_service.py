from google.cloud import bigquery
from google.oauth2 import service_account
from typing import List, Dict, Optional
import os
from app.config import settings


class BigQueryService:
    """Service for interacting with Google Patents Public Datasets via BigQuery."""

    def __init__(self):
        """Initialize BigQuery client with service account credentials."""
        self.dataset = settings.bigquery_dataset
        self.table = settings.bigquery_table
        self.client = None
        self._initialize_client()

    def _initialize_client(self):
        """Initialize BigQuery client with proper authentication."""
        try:
            if settings.google_application_credentials and os.path.exists(settings.google_application_credentials):
                credentials = service_account.Credentials.from_service_account_file(
                    settings.google_application_credentials
                )
                self.client = bigquery.Client(
                    credentials=credentials,
                    project=settings.gcp_project_id
                )
            else:
                # Fall back to application default credentials
                self.client = bigquery.Client(project=settings.gcp_project_id)
        except Exception as e:
            print(f"Warning: Failed to initialize BigQuery client: {e}")
            self.client = None

    def _build_query(
        self,
        keywords: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        inventor: Optional[str] = None,
        applicant: Optional[str] = None,
        country: Optional[str] = None,
        limit: int = 50
    ) -> tuple[str, List[bigquery.ScalarQueryParameter]]:
        """Build BigQuery SQL query with filters."""

        # Base query - using the correct schema for patents-public-data
        # Note: Claims excluded to reduce query costs
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

        parameters = []

        # Keyword search in title and abstract
        if keywords:
            query += """
            AND (
                LOWER(title_localized[SAFE_OFFSET(0)].text) LIKE LOWER(@keywords)
                OR LOWER(abstract_localized[SAFE_OFFSET(0)].text) LIKE LOWER(@keywords)
            )
            """
            parameters.append(
                bigquery.ScalarQueryParameter("keywords", "STRING", f"%{keywords}%")
            )

        # Date range filter (publication_date is stored as INT64 in YYYYMMDD format)
        if date_from:
            # Convert YYYY-MM-DD to YYYYMMDD integer format
            date_from_int = int(date_from.replace("-", ""))
            query += " AND publication_date >= @date_from"
            parameters.append(
                bigquery.ScalarQueryParameter("date_from", "INT64", date_from_int)
            )

        if date_to:
            # Convert YYYY-MM-DD to YYYYMMDD integer format
            date_to_int = int(date_to.replace("-", ""))
            query += " AND publication_date <= @date_to"
            parameters.append(
                bigquery.ScalarQueryParameter("date_to", "INT64", date_to_int)
            )

        # Inventor filter
        if inventor:
            query += """
            AND EXISTS (
                SELECT 1 FROM UNNEST(inventor_harmonized) as inv
                WHERE LOWER(inv.name) LIKE LOWER(@inventor)
            )
            """
            parameters.append(
                bigquery.ScalarQueryParameter("inventor", "STRING", f"%{inventor}%")
            )

        # Applicant/Assignee filter
        if applicant:
            query += """
            AND EXISTS (
                SELECT 1 FROM UNNEST(assignee_harmonized) as asn
                WHERE LOWER(asn.name) LIKE LOWER(@applicant)
            )
            """
            parameters.append(
                bigquery.ScalarQueryParameter("applicant", "STRING", f"%{applicant}%")
            )

        # Country filter
        if country:
            query += " AND country_code = @country"
            parameters.append(
                bigquery.ScalarQueryParameter("country", "STRING", country.upper())
            )

        # Order by most recent first
        query += " ORDER BY publication_date DESC"

        # Limit results
        query += " LIMIT @limit"
        parameters.append(
            bigquery.ScalarQueryParameter("limit", "INT64", min(limit, settings.max_results_limit))
        )

        return query, parameters

    async def search_patents(
        self,
        keywords: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        inventor: Optional[str] = None,
        applicant: Optional[str] = None,
        country: Optional[str] = None,
        limit: int = None
    ) -> Dict[str, any]:
        """
        Search Google Patents using BigQuery.

        Args:
            keywords: Search keywords
            date_from: Start date (YYYY-MM-DD)
            date_to: End date (YYYY-MM-DD)
            inventor: Inventor name filter
            applicant: Applicant/assignee name filter
            country: Country code filter (e.g., 'US', 'JP', 'EP')
            limit: Maximum number of results

        Returns:
            Dict with 'results' (list of patents) and 'count' (total results)
        """
        if not self.client:
            raise Exception("BigQuery client not initialized. Please check your GCP credentials.")

        if limit is None:
            limit = settings.default_results_limit

        # Build query
        query, parameters = self._build_query(
            keywords=keywords,
            date_from=date_from,
            date_to=date_to,
            inventor=inventor,
            applicant=applicant,
            country=country,
            limit=limit
        )

        # Configure query job
        job_config = bigquery.QueryJobConfig(
            query_parameters=parameters
        )

        try:
            # Execute query
            query_job = self.client.query(query, job_config=job_config)
            results = query_job.result()

            # Process results
            patents = []
            for row in results:
                # Extract inventor names
                inventors = []
                if row.inventor_harmonized:
                    inventors = [inv.get('name', '') for inv in row.inventor_harmonized if inv.get('name')]

                # Extract assignee names
                assignees = []
                if row.assignee_harmonized:
                    assignees = [asn.get('name', '') for asn in row.assignee_harmonized if asn.get('name')]

                # Convert YYYYMMDD integer to YYYY-MM-DD string
                filing_date_str = None
                if row.filing_date:
                    date_str = str(row.filing_date)
                    if len(date_str) == 8:
                        filing_date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

                publication_date_str = None
                if row.publication_date:
                    date_str = str(row.publication_date)
                    if len(date_str) == 8:
                        publication_date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

                patent = {
                    "source": "google_patents",
                    "publication_number": row.publication_number,
                    "title": row.title or "No title available",
                    "abstract": row.abstract or "No abstract available",
                    "description": row.description or "",
                    "filing_date": filing_date_str,
                    "publication_date": publication_date_str,
                    "inventors": inventors,
                    "assignees": assignees,
                    "country_code": row.country_code,
                    "url": f"https://patents.google.com/patent/{row.publication_number}"
                }
                patents.append(patent)

            return {
                "results": patents,
                "count": len(patents),
                "query": keywords
            }

        except Exception as e:
            raise Exception(f"BigQuery search failed: {str(e)}")

    async def check_connection(self) -> bool:
        """Check if BigQuery connection is working."""
        if not self.client:
            return False

        try:
            # Try a simple query
            query = f"SELECT 1 FROM `{self.dataset}.{self.table}` LIMIT 1"
            query_job = self.client.query(query)
            query_job.result()
            return True
        except Exception as e:
            print(f"BigQuery connection check failed: {e}")
            return False

    def estimate_query_cost(self, query: str) -> Dict[str, any]:
        """Estimate the cost of running a query (dry run)."""
        if not self.client:
            return {"error": "Client not initialized"}

        try:
            job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
            query_job = self.client.query(query, job_config=job_config)

            # Get bytes processed
            bytes_processed = query_job.total_bytes_processed
            gb_processed = bytes_processed / (1024 ** 3)

            return {
                "bytes_processed": bytes_processed,
                "gb_processed": round(gb_processed, 4),
                "estimated_cost_usd": round(gb_processed * 0.005, 4)  # $5 per TB
            }
        except Exception as e:
            return {"error": str(e)}
