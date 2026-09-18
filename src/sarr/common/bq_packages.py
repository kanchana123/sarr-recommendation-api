"""Shared BigQuery SQL for PyPI package rows (ETL extract + online metadata)."""

from __future__ import annotations

from sarr.common.config import Settings

_PYPI_PACKAGES_CTE = """
WITH per_file AS (
  SELECT
    name,
    version,
    summary,
    description,
    license,
    keywords,
    classifiers,
    requires_python,
    requires_dist,
    home_page,
    project_urls,
    upload_time,
    ROW_NUMBER() OVER (
      PARTITION BY name, version
      ORDER BY upload_time DESC
    ) AS file_rn
  FROM `{pypi_project}.{pypi_dataset}.distribution_metadata`
  WHERE upload_time IS NOT NULL
),
per_version AS (
  SELECT * EXCEPT (file_rn)
  FROM per_file
  WHERE file_rn = 1
),
latest AS (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY name
      ORDER BY upload_time DESC, version DESC
    ) AS pkg_rn
  FROM per_version
),
packages AS (
  SELECT * EXCEPT (pkg_rn)
  FROM latest
  WHERE pkg_rn = 1
)
"""

_PYPI_ROW_SELECT = """
  SELECT
    'Pypi' AS platform,
    p.name,
    COALESCE(
      NULLIF(TRIM(p.description), ''),
      NULLIF(TRIM(p.summary), '')
    ) AS description,
    p.summary,
    p.keywords,
    p.classifiers,
    p.requires_python,
    p.requires_dist AS dependencies,
    p.license AS licenses,
    p.home_page AS homepage_url,
    p.project_urls,
    p.upload_time AS latest_release_publish_timestamp,
    CAST(COALESCE(li.sourcerank, 0) AS INT64) AS sourcerank,
    CAST(COALESCE(li.dependent_projects_count, 0) AS INT64) AS dependent_projects_count,
    CAST(COALESCE(li.versions_count, 0) AS INT64) AS versions_count,
    CAST(COALESCE(li.dependent_repositories_count, 0) AS INT64) AS dependent_repositories_count,
    li.language,
    CAST(COALESCE(r.stars_count, 0) AS INT64) AS repository_stars_count,
    CAST(COALESCE(r.forks_count, 0) AS INT64) AS repository_forks_count,
    COALESCE(li.keywords, '') AS libraries_io_keywords
  FROM packages AS p
  {libraries_join} `{libraries_project}.{libraries_dataset}.projects` AS li
    ON li.platform = 'Pypi'
    AND LOWER(REPLACE(li.name, '_', '-')) = LOWER(REPLACE(p.name, '_', '-'))
  LEFT JOIN `{libraries_project}.{libraries_dataset}.repositories` AS r
    ON li.repository_id = r.id
"""


def _pypi_text_filter_sql(min_len: int) -> str:
    return f"""(
    (
      p.description IS NOT NULL AND TRIM(p.description) != ''
      AND LENGTH(TRIM(p.description)) > {min_len}
    )
    OR (
      p.summary IS NOT NULL AND TRIM(p.summary) != ''
      AND LENGTH(TRIM(p.summary)) > {min_len}
    )
  )"""


def _pypi_popularity_filter_sql(settings: Settings) -> str:
    if not settings.etl_require_any_popularity:
        return ""
    thresholds: list[str] = []
    if settings.etl_min_stars > 0:
        thresholds.append(f"COALESCE(r.stars_count, 0) >= {int(settings.etl_min_stars)}")
    if settings.etl_min_forks > 0:
        thresholds.append(f"COALESCE(r.forks_count, 0) >= {int(settings.etl_min_forks)}")
    if settings.etl_min_sourcerank > 0:
        thresholds.append(f"COALESCE(li.sourcerank, 0) >= {int(settings.etl_min_sourcerank)}")
    if settings.etl_min_dependent_projects > 0:
        dep_min = int(settings.etl_min_dependent_projects)
        thresholds.append(
            "COALESCE(li.dependent_projects_count, 0) >= "
            f"{dep_min}"
        )
    if settings.etl_min_long_description_length > 0:
        long_len = int(settings.etl_min_long_description_length)
        thresholds.append(
            f"GREATEST("
            f"LENGTH(TRIM(COALESCE(p.description, ''))), "
            f"LENGTH(TRIM(COALESCE(p.summary, '')))"
            f") >= {long_len}"
        )
    if not thresholds:
        return ""
    joined = "\n      OR ".join(thresholds)
    return f"(\n      {joined}\n    )"


def pypi_etl_where_clause(settings: Settings) -> str:
    min_len = max(1, settings.etl_min_description_length)
    parts = [
        _pypi_text_filter_sql(min_len),
        "p.upload_time > TIMESTAMP(@last_update_date)",
    ]
    if settings.etl_active_within_days and settings.etl_active_within_days > 0:
        days = int(settings.etl_active_within_days)
        parts.append(
            f"p.upload_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)"
        )
    popularity = _pypi_popularity_filter_sql(settings)
    if popularity:
        parts.append(popularity)
    return "\n    AND ".join(parts)


def _format_pypi_cte(settings: Settings) -> str:
    return _PYPI_PACKAGES_CTE.format(
        pypi_project=settings.bq_pypi_project,
        pypi_dataset=settings.bq_pypi_dataset,
    )


def _format_row_select(settings: Settings) -> str:
    libraries_join = "INNER JOIN" if settings.etl_require_libraries_io else "LEFT JOIN"
    return _PYPI_ROW_SELECT.format(
        libraries_join=libraries_join,
        libraries_project=settings.bq_libraries_project,
        libraries_dataset=settings.bq_libraries_dataset,
    )


def build_pypi_extract_sql(settings: Settings, *, for_count: bool) -> str:
    where_clause = pypi_etl_where_clause(settings)
    candidate = (
        _format_row_select(settings)
        + f"""
  WHERE {where_clause}
"""
    )
    cap = _pypi_cap_cte() if settings.etl_max_packages else ""
    source = "eligible" if settings.etl_max_packages else "candidates"
    body = (
        _format_pypi_cte(settings)
        + f",\ncandidates AS (\n{candidate}\n)\n"
        + cap
    )
    if for_count:
        return body + f"\nSELECT COUNT(*) AS n\nFROM {source}\n"
    return body + f"\nSELECT *\nFROM {source}\nORDER BY latest_release_publish_timestamp ASC\n"


def _pypi_cap_cte() -> str:
    return """
, ranked AS (
  SELECT
    *,
    ROW_NUMBER() OVER (
      ORDER BY
        repository_stars_count DESC,
        repository_forks_count DESC,
        sourcerank DESC,
        dependent_projects_count DESC,
        latest_release_publish_timestamp DESC
    ) AS _etl_rank
  FROM candidates
),
eligible AS (
  SELECT * EXCEPT (_etl_rank)
  FROM ranked
  WHERE @max_packages IS NULL OR _etl_rank <= @max_packages
)
"""


def build_pypi_fetch_by_names_sql(settings: Settings) -> str:
    """Latest PyPI + Libraries.io row for each requested package name."""
    return (
        _format_pypi_cte(settings)
        + ",\n"
        + "requested AS (\n"
        + "  SELECT DISTINCT LOWER(REPLACE(n, '_', '-')) AS norm\n"
        + "  FROM UNNEST(@names) AS n\n"
        + ")\n"
        + "SELECT pkg_rows.*\n"
        + "FROM (\n"
        + _format_row_select(settings)
        + "\n) AS pkg_rows\n"
        + "INNER JOIN requested AS req\n"
        + "  ON LOWER(REPLACE(pkg_rows.name, '_', '-')) = req.norm\n"
    )
