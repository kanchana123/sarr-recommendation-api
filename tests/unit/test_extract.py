"""Unit tests for BigQuery mapping / SQL builder."""

from __future__ import annotations

from datetime import datetime

import pytest

from sarr.common.config import Settings
from sarr.etl.extract import build_count_sql, build_extract_sql, row_to_package


@pytest.mark.unit
def test_build_extract_sql_uses_pypi_by_default() -> None:
    settings = Settings(
        gcp_project_id="my-billing-project",
        etl_extract_source="pypi",
        etl_max_packages=700_000,
    )
    sql = build_extract_sql(settings)
    assert "`bigquery-public-data.pypi.distribution_metadata`" in sql
    assert "bigquery-public-data.libraries_io.projects" in sql
    assert "upload_time > TIMESTAMP(@last_update_date)" in sql
    assert "repository_forks_count" in sql


@pytest.mark.unit
def test_build_extract_sql_libraries_io_legacy() -> None:
    settings = Settings(
        gcp_project_id="my-billing-project",
        etl_extract_source="libraries_io",
        bq_source_project="bigquery-public-data",
        bq_dataset="libraries_io",
    )
    sql = build_extract_sql(settings)
    assert "`bigquery-public-data.libraries_io.projects`" in sql
    assert "`bigquery-public-data.libraries_io.repositories`" in sql
    assert "p.platform = 'Pypi'" in sql
    assert "latest_release_publish_timestamp > TIMESTAMP(@last_update_date)" in sql


@pytest.mark.unit
def test_build_count_sql_matches_extract_source() -> None:
    pypi_settings = Settings(gcp_project_id="x", etl_extract_source="pypi")
    assert "distribution_metadata" in build_count_sql(pypi_settings)
    lib_settings = Settings(gcp_project_id="x", etl_extract_source="libraries_io")
    assert "libraries_io" in build_count_sql(lib_settings)


@pytest.mark.unit
def test_build_pypi_sql_applies_corpus_filters() -> None:
    settings = Settings(
        gcp_project_id="x",
        etl_extract_source="pypi",
        etl_require_libraries_io=True,
        etl_max_packages=650_000,
        etl_min_description_length=40,
        etl_active_within_days=1825,
        etl_min_stars=1,
        etl_min_forks=1,
    )
    sql = build_extract_sql(settings)
    assert "INNER JOIN" in sql
    assert "_etl_rank" in sql
    assert "LENGTH(TRIM(p.description)) > 40" in sql
    assert "INTERVAL 1825 DAY" in sql
    assert "stars_count, 0) >= 1" in sql
    assert "forks_count, 0) >= 1" in sql


@pytest.mark.unit
def test_row_to_package_maps_pypi_columns() -> None:
    package = row_to_package(
        {
            "platform": "Pypi",
            "name": "Requests",
            "description": "Python HTTP for Humans.",
            "summary": "HTTP library",
            "homepage_url": "https://requests.readthedocs.io",
            "project_urls": [{"label": "Source", "url": "https://github.com/psf/requests"}],
            "licenses": "Apache-2.0",
            "requires_python": ">=3.8",
            "requires_dist": ["urllib3 (>=1.21.1)", "certifi (>=2017.4.17)"],
            "classifiers": ["Development Status :: 5 - Production/Stable"],
            "latest_release_publish_timestamp": datetime(2024, 1, 15),
            "repository_stars_count": 52000,
            "repository_forks_count": 9400,
            "keywords": "http, requests, client",
        }
    )
    assert package.name == "requests"
    assert package.summary == "Python HTTP for Humans."
    assert package.stars == 52000
    assert package.forks == 9400
    assert package.license == "Apache-2.0"
    assert package.repo_url == "https://github.com/psf/requests"
    assert package.pypi_url == "https://pypi.org/project/requests/"
    assert package.keywords == ["http", "requests", "client"]
    assert package.dependencies == ["urllib3 (>=1.21.1)", "certifi (>=2017.4.17)"]
    assert package.update_date is not None


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
