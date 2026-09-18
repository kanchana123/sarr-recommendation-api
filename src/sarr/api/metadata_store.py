"""Fetch package metadata from BigQuery for online search / RAG."""

from __future__ import annotations

from typing import Any

from sarr.common.bq_packages import build_pypi_fetch_by_names_sql
from sarr.common.config import Settings, get_settings
from sarr.etl.extract import get_bigquery_client, row_to_package
from sarr.etl.transform import to_payload


class PackageMetadataStore:
    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = get_bigquery_client(self.settings)
        return self._client

    def fetch_by_names(self, names: list[str]) -> dict[str, dict[str, Any]]:
        """Return Qdrant-style payload dicts keyed by normalized package name."""
        if not names:
            return {}
        if not self.settings.gcp_project_id:
            raise ValueError("GCP_PROJECT_ID is required to hydrate metadata from BigQuery.")

        from google.cloud.bigquery import ArrayQueryParameter, QueryJobConfig

        sql = build_pypi_fetch_by_names_sql(self.settings)
        job_config = QueryJobConfig(
            query_parameters=[ArrayQueryParameter("names", "STRING", list(names))]
        )
        rows = self.client.query(sql, job_config=job_config)
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            package = row_to_package(dict(row.items()) if hasattr(row, "items") else dict(row))
            out[package.name] = to_payload(package)
        return out
