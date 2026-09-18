"""Unit tests for shared PyPI BigQuery SQL builders."""

from __future__ import annotations

import pytest

from sarr.common.bq_packages import (
    build_pypi_extract_sql,
    build_pypi_fetch_by_names_sql,
    pypi_etl_where_clause,
)
from sarr.common.config import Settings


@pytest.mark.unit
def test_fetch_by_names_sql_avoids_reserved_rows_alias() -> None:
    sql = build_pypi_fetch_by_names_sql(Settings(gcp_project_id="x"))
    assert " AS rows" not in sql
    assert "pkg_rows" in sql
    assert "UNNEST(@names)" in sql


@pytest.mark.unit
def test_fetch_by_names_includes_libraries_join() -> None:
    sql = build_pypi_fetch_by_names_sql(Settings(gcp_project_id="x"))
    assert "libraries_io.repositories" in sql
    assert "repository_forks_count" in sql


@pytest.mark.unit
def test_etl_where_includes_popularity_or_gate() -> None:
    settings = Settings(
        gcp_project_id="x",
        etl_require_any_popularity=True,
        etl_min_stars=1,
        etl_min_forks=2,
        etl_min_long_description_length=80,
    )
    clause = pypi_etl_where_clause(settings)
    assert "stars_count, 0) >= 1" in clause
    assert "forks_count, 0) >= 2" in clause
    assert "GREATEST(" in clause


@pytest.mark.unit
def test_etl_where_includes_active_within_days() -> None:
    settings = Settings(gcp_project_id="x", etl_active_within_days=365)
    clause = pypi_etl_where_clause(settings)
    assert "INTERVAL 365 DAY" in clause


@pytest.mark.unit
def test_extract_sql_applies_max_packages_cap() -> None:
    sql = build_pypi_extract_sql(
        Settings(gcp_project_id="x", etl_max_packages=650_000),
        for_count=False,
    )
    assert "_etl_rank" in sql
    assert "@max_packages" in sql


@pytest.mark.unit
def test_extract_count_sql_matches_extract_shape() -> None:
    settings = Settings(gcp_project_id="x", etl_max_packages=100)
    extract = build_pypi_extract_sql(settings, for_count=False)
    count = build_pypi_extract_sql(settings, for_count=True)
    assert "SELECT COUNT(*) AS n" in count
    assert "ORDER BY latest_release_publish_timestamp ASC" in extract
    assert "ORDER BY latest_release_publish_timestamp ASC" not in count
