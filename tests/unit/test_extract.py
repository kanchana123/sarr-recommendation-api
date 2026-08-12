"""Unit tests for Libraries.io BigQuery mapping / SQL builder."""

from __future__ import annotations

from datetime import datetime

import pytest

from sarr.common.config import Settings
from sarr.etl.extract import build_extract_sql, row_to_package


@pytest.mark.unit
def test_build_extract_sql_uses_libraries_io() -> None:
    settings = Settings(
        gcp_project_id="my-billing-project",
        bq_source_project="bigquery-public-data",
        bq_dataset="libraries_io",
    )
    sql = build_extract_sql(settings)
    assert "`bigquery-public-data.libraries_io.projects`" in sql
    assert "`bigquery-public-data.libraries_io.repositories`" in sql
    assert "p.platform = 'Pypi'" in sql
    assert "latest_release_publish_timestamp > TIMESTAMP(@last_update_date)" in sql


@pytest.mark.unit
def test_row_to_package_maps_libraries_io_columns() -> None:
    package = row_to_package(
        {
            "platform": "Pypi",
            "name": "Requests",
            "description": "Python HTTP for Humans.",
            "homepage_url": "https://requests.readthedocs.io",
            "repository_url": "https://github.com/psf/requests",
            "licenses": ["Apache-2.0"],
            "sourcerank": 28,
            "dependent_projects_count": 50000,
            "versions_count": 140,
            "dependent_repositories_count": 100000,
            "language": "Python",
            "latest_release_publish_timestamp": datetime(2024, 1, 15),
            "repository_stars_count": 52000,
            "keywords": "http, requests, client",
        }
    )
    assert package.name == "requests"
    assert package.summary == "Python HTTP for Humans."
    assert package.stars == 52000
    assert package.sourcerank == 28
    assert package.dependent_projects_count == 50000
    assert package.license == "Apache-2.0"
    assert package.repo_url == "https://github.com/psf/requests"
    assert package.pypi_url == "https://pypi.org/project/requests/"
    assert package.keywords == ["http", "requests", "client"]
    assert package.update_date is not None
