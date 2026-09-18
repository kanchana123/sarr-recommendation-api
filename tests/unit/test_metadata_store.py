"""Unit tests for BigQuery metadata hydration."""

from __future__ import annotations

from datetime import datetime

import pytest

from sarr.api.metadata_store import PackageMetadataStore
from sarr.common.config import Settings


class FakeRow:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def items(self) -> list[tuple[str, object]]:
        return list(self._data.items())


class FakeBqClient:
    def __init__(self, rows: list[FakeRow]) -> None:
        self.rows = rows
        self.last_sql: str | None = None
        self.last_names: list[str] | None = None

    def query(self, sql: str, job_config=None):  # noqa: ANN001
        self.last_sql = sql
        if job_config and job_config.query_parameters:
            self.last_names = list(job_config.query_parameters[0].values)
        return self.rows


@pytest.mark.unit
def test_fetch_by_names_maps_rows_to_payloads() -> None:
    settings = Settings(gcp_project_id="billing-project")
    client = FakeBqClient(
        [
            FakeRow(
                {
                    "name": "requests",
                    "description": "HTTP for Humans.",
                    "summary": "HTTP library",
                    "repository_stars_count": 52000,
                    "repository_forks_count": 100,
                    "latest_release_publish_timestamp": datetime(2024, 1, 1),
                    "licenses": "Apache-2.0",
                }
            )
        ]
    )
    store = PackageMetadataStore(settings, client=client)
    payloads = store.fetch_by_names(["requests"])
    assert "requests" in payloads
    assert payloads["requests"]["name"] == "requests"
    assert payloads["requests"]["summary"] == "HTTP for Humans."
    assert payloads["requests"]["stars"] == 52000
    assert client.last_sql is not None
    assert "pkg_rows" in client.last_sql
    assert client.last_names == ["requests"]


@pytest.mark.unit
def test_fetch_by_names_requires_gcp_project() -> None:
    store = PackageMetadataStore(Settings(gcp_project_id=""), client=FakeBqClient([]))
    with pytest.raises(ValueError, match="GCP_PROJECT_ID"):
        store.fetch_by_names(["requests"])
