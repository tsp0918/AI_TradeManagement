import pandas as pd
import json
from typing import List, Dict
from io import StringIO, BytesIO


class ExportService:
    """Service for exporting patent data to various formats."""

    def export_to_csv(self, patents: List[Dict]) -> str:
        """
        Export patent data to CSV format.

        Args:
            patents: List of patent dictionaries

        Returns:
            CSV string
        """
        if not patents:
            return ""

        # Flatten the data for CSV
        flattened_patents = []
        for patent in patents:
            flat_patent = {
                "Publication Number": patent.get("publication_number", ""),
                "Title": patent.get("title", ""),
                "Abstract": patent.get("abstract", ""),
                "Description": patent.get("description", ""),
                "Filing Date": patent.get("filing_date", ""),
                "Publication Date": patent.get("publication_date", ""),
                "Country Code": patent.get("country_code", ""),
                "URL": patent.get("url", ""),
            }

            # Handle inventors (list to comma-separated string)
            if "inventors" in patent and isinstance(patent["inventors"], list):
                flat_patent["Inventors"] = "; ".join(patent["inventors"])
            else:
                flat_patent["Inventors"] = ""

            # Handle assignees (list to comma-separated string)
            if "assignees" in patent and isinstance(patent["assignees"], list):
                flat_patent["Assignees"] = "; ".join(patent["assignees"])
            else:
                flat_patent["Assignees"] = ""

            # Add notes if available (for favorites)
            if "notes" in patent:
                flat_patent["Notes"] = patent.get("notes", "")

            flattened_patents.append(flat_patent)

        # Create DataFrame and convert to CSV
        df = pd.DataFrame(flattened_patents)
        return df.to_csv(index=False)

    def export_to_json(self, patents: List[Dict]) -> str:
        """
        Export patent data to JSON format.

        Args:
            patents: List of patent dictionaries

        Returns:
            JSON string
        """
        return json.dumps(patents, indent=2, ensure_ascii=False)

    def create_csv_response(self, csv_string: str, filename: str = "patents.csv") -> tuple:
        """
        Prepare CSV string for HTTP response.

        Args:
            csv_string: CSV data as string
            filename: Filename for download

        Returns:
            Tuple of (content, headers)
        """
        headers = {
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Type": "text/csv; charset=utf-8"
        }
        return csv_string, headers

    def create_json_response(self, json_string: str, filename: str = "patents.json") -> tuple:
        """
        Prepare JSON string for HTTP response.

        Args:
            json_string: JSON data as string
            filename: Filename for download

        Returns:
            Tuple of (content, headers)
        """
        headers = {
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Type": "application/json; charset=utf-8"
        }
        return json_string, headers

    def prepare_favorites_for_export(self, favorites: List) -> List[Dict]:
        """
        Prepare favorite patents for export by parsing JSON fields.

        Args:
            favorites: List of FavoritePatent objects

        Returns:
            List of dictionaries ready for export
        """
        export_data = []

        for fav in favorites:
            patent_dict = {
                "publication_number": fav.publication_number,
                "title": fav.title,
                "abstract": fav.abstract,
                "filing_date": fav.filing_date.isoformat() if fav.filing_date else "",
                "publication_date": fav.publication_date.isoformat() if fav.publication_date else "",
                "url": fav.url,
                "notes": fav.notes or "",
                "added_at": fav.added_at.isoformat()
            }

            # Parse JSON fields
            if fav.inventors:
                try:
                    patent_dict["inventors"] = json.loads(fav.inventors)
                except json.JSONDecodeError:
                    patent_dict["inventors"] = []
            else:
                patent_dict["inventors"] = []

            if fav.applicants:
                try:
                    patent_dict["assignees"] = json.loads(fav.applicants)
                except json.JSONDecodeError:
                    patent_dict["assignees"] = []
            else:
                patent_dict["assignees"] = []

            export_data.append(patent_dict)

        return export_data

    def export_use_cases_to_csv(self, patent_info: Dict, use_cases: List[Dict]) -> str:
        """
        Export use cases for a patent to CSV format.

        Args:
            patent_info: Dictionary with patent information (publication_number, title, etc.)
            use_cases: List of use case dictionaries

        Returns:
            CSV string
        """
        if not use_cases:
            return ""

        # Prepare data for CSV
        rows = []
        for i, uc in enumerate(use_cases, 1):
            row = {
                "特許番号": patent_info.get("publication_number", ""),
                "特許タイトル": patent_info.get("title", ""),
                "用途番号": i,
                "用途内容": uc.get("text", ""),
                "抽出モデル": uc.get("model", ""),
                "抽出日時": uc.get("created_at", "")
            }
            rows.append(row)

        # Create DataFrame and convert to CSV
        df = pd.DataFrame(rows)
        return df.to_csv(index=False, encoding='utf-8-sig')  # BOM for Excel compatibility

    def export_multiple_patents_use_cases_to_csv(self, patents_with_use_cases: List[Dict]) -> str:
        """
        Export use cases for multiple patents to CSV format.

        Args:
            patents_with_use_cases: List of dictionaries, each containing patent info and use_cases

        Returns:
            CSV string
        """
        if not patents_with_use_cases:
            return ""

        # Prepare data for CSV
        rows = []
        for patent_data in patents_with_use_cases:
            patent_info = patent_data.get("patent", {})
            use_cases = patent_data.get("use_cases", [])

            for i, uc in enumerate(use_cases, 1):
                row = {
                    "特許番号": patent_info.get("publication_number", ""),
                    "特許タイトル": patent_info.get("title", ""),
                    "用途番号": i,
                    "用途内容": uc.get("text", ""),
                    "抽出モデル": uc.get("model", ""),
                    "抽出日時": uc.get("created_at", "")
                }
                rows.append(row)

        # Create DataFrame and convert to CSV
        df = pd.DataFrame(rows)
        return df.to_csv(index=False, encoding='utf-8-sig')  # BOM for Excel compatibility
