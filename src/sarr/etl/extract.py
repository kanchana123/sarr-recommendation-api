"""BigQuery extract via google.cloud.bigquery.Client.

Default source: PyPI public dataset (~800k+ projects, current metadata).
Optional legacy source: Libraries.io (stale snapshot, ~168k PyPI rows).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any, Literal

from sarr.common.config import Settings, get_settings
from sarr.common.schemas import PackageRecord
from sarr.etl.watermark import parse_watermark

ExtractSource = Literal["pypi", "libraries_io"]

# Latest metadata row per PyPI project; optional Libraries.io join for stars / SourceRank.
EXTRACT_SQL_PYPI = """
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
  COALESCE(li.keywords, '') AS libraries_io_keywords
FROM packages AS p
LEFT JOIN `{libraries_project}.{libraries_dataset}.projects` AS li
  ON li.platform = 'Pypi'
  AND LOWER(REPLACE(li.name, '_', '-')) = LOWER(REPLACE(p.name, '_', '-'))
LEFT JOIN `{libraries_project}.{libraries_dataset}.repositories` AS r
  ON li.repository_id = r.id
WHERE (
    (p.description IS NOT NULL AND TRIM(p.description) != '' AND LENGTH(TRIM(p.description)) > 5)
    OR (p.summary IS NOT NULL AND TRIM(p.summary) != '' AND LENGTH(TRIM(p.summary)) > 5)
  )
  AND p.upload_time > TIMESTAMP(@last_update_date)
ORDER BY p.upload_time ASC
"""

# Legacy Libraries.io-only extract (deprecated for full corpus).
EXTRACT_SQL_LIBRARIES_IO = """
SELECT
  p.platform,
  p.name,
  p.description,
  p.homepage_url,
  p.repository_url,
  p.licenses,
  CAST(p.sourcerank AS INT64) AS sourcerank,
  CAST(p.dependent_projects_count AS INT64) AS dependent_projects_count,
  CAST(p.versions_count AS INT64) AS versions_count,
  CAST(p.dependent_repositories_count AS INT64) AS dependent_repositories_count,
  p.language,
  p.latest_release_publish_timestamp AS latest_release_publish_timestamp,
  CAST(COALESCE(r.stars_count, 0) AS INT64) AS repository_stars_count,
  COALESCE(p.keywords, '') AS keywords
FROM `{source_project}.{dataset}.projects` AS p
LEFT JOIN `{source_project}.{dataset}.repositories` AS r
  ON p.repository_id = r.id
WHERE p.platform = 'Pypi'
  AND p.description IS NOT NULL
  AND TRIM(p.description) != ''
  AND LENGTH(p.description) > 5
  AND p.latest_release_publish_timestamp IS NOT NULL
  AND p.latest_release_publish_timestamp > TIMESTAMP(@last_update_date)
ORDER BY p.latest_release_publish_timestamp ASC
"""

COUNT_SQL_PYPI = """
WITH per_file AS (
  SELECT
    name,
    version,
    summary,
    description,
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
SELECT COUNT(*) AS n
FROM packages AS p
WHERE (
    (p.description IS NOT NULL AND TRIM(p.description) != '' AND LENGTH(TRIM(p.description)) > 5)
    OR (p.summary IS NOT NULL AND TRIM(p.summary) != '' AND LENGTH(TRIM(p.summary)) > 5)
  )
  AND p.upload_time > TIMESTAMP(@last_update_date)
"""

COUNT_SQL_LIBRARIES_IO = """
SELECT COUNT(*) AS n
FROM `{source_project}.{dataset}.projects` AS p
WHERE p.platform = 'Pypi'
  AND p.description IS NOT NULL
  AND TRIM(p.description) != ''
  AND LENGTH(p.description) > 5
  AND p.latest_release_publish_timestamp IS NOT NULL
  AND p.latest_release_publish_timestamp > TIMESTAMP(@last_update_date)
"""


def _extract_source(settings: Settings) -> ExtractSource:
    source = (settings.etl_extract_source or "pypi").strip().lower()
    if source in ("pypi", "libraries_io"):
        return source  # type: ignore[return-value]
    raise ValueError(
        f"Invalid ETL_EXTRACT_SOURCE={settings.etl_extract_source!r}. "
        "Use 'pypi' or 'libraries_io'."
    )


def build_extract_sql(settings: Settings) -> str:
    source = _extract_source(settings)
    if source == "pypi":
        return EXTRACT_SQL_PYPI.format(
            pypi_project=settings.bq_pypi_project,
            pypi_dataset=settings.bq_pypi_dataset,
            libraries_project=settings.bq_libraries_project,
            libraries_dataset=settings.bq_libraries_dataset,
        )
    return EXTRACT_SQL_LIBRARIES_IO.format(
        source_project=settings.bq_source_project,
        dataset=settings.bq_dataset,
    )


def build_count_sql(settings: Settings) -> str:
    source = _extract_source(settings)
    if source == "pypi":
        return COUNT_SQL_PYPI.format(
            pypi_project=settings.bq_pypi_project,
            pypi_dataset=settings.bq_pypi_dataset,
        )
    return COUNT_SQL_LIBRARIES_IO.format(
        source_project=settings.bq_source_project,
        dataset=settings.bq_dataset,
    )


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def _as_license(value: Any) -> str | None:
    items = _as_list(value)
    if not items:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None
    return ", ".join(items)


def _as_datetime(value: Any) -> datetime | str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return value
    return value


def _repo_from_project_urls(value: Any) -> str | None:
    if not value:
        return None
    candidates: list[tuple[int, str]] = []
    items = value if isinstance(value, list) else [value]
    for item in items:
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("name") or "").lower()
            url = str(item.get("url") or item.get("href") or "").strip()
        else:
            label = ""
            url = str(item).strip()
        if not url.startswith("http"):
            continue
        priority = 0
        if any(token in label for token in ("source", "repository", "code", "github")):
            priority = 3
        elif "homepage" in label:
            priority = 1
        if "github.com" in url or "gitlab.com" in url or "bitbucket.org" in url:
            priority = max(priority, 2)
        candidates.append((priority, url))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][1]


def row_to_package(row: dict[str, Any]) -> PackageRecord:
    name = str(row["name"]).strip().lower().replace("_", "-")
    release_ts = _as_datetime(
        row.get("latest_release_publish_timestamp") or row.get("update_date")
    )
    keywords = _as_list(row.get("keywords"))
    if not keywords and row.get("libraries_io_keywords"):
        keywords = _as_list(row.get("libraries_io_keywords"))
    repo_url = row.get("repository_url") or _repo_from_project_urls(row.get("project_urls"))
    return PackageRecord(
        name=name,
        summary=row.get("description") or row.get("summary"),
        keywords=keywords,
        dependencies=_as_list(row.get("dependencies") or row.get("requires_dist")),
        classifiers=_as_list(row.get("classifiers")),
        stars=int(row.get("repository_stars_count") or row.get("stars") or 0),
        forks=int(row.get("forks") or 0),
        downloads_30d=row.get("downloads_30d"),
        last_commit=row.get("last_commit"),
        latest_release=release_ts,
        license=_as_license(row.get("licenses") or row.get("license")),
        requires_python=row.get("requires_python"),
        repo_url=repo_url,
        homepage_url=row.get("homepage_url") or row.get("home_page"),
        pypi_url=row.get("pypi_url") or f"https://pypi.org/project/{name}/",
        update_date=release_ts,
        sourcerank=int(row.get("sourcerank") or 0),
        dependent_projects_count=int(row.get("dependent_projects_count") or 0),
        versions_count=int(row.get("versions_count") or 0),
        dependent_repositories_count=int(row.get("dependent_repositories_count") or 0),
        language=row.get("language"),
        platform=row.get("platform") or "Pypi",
    )


def get_bigquery_client(settings: Settings | None = None) -> Any:
    """Create a BigQuery client. Uses GCP_PROJECT_ID as the billing project."""
    from google.cloud.bigquery import Client

    cfg = settings or get_settings()
    if not cfg.gcp_project_id:
        raise ValueError(
            "GCP_PROJECT_ID is required. Set it to your GCP project used for "
            "BigQuery billing/quota (not the public dataset project)."
        )
    return Client(project=cfg.gcp_project_id)


def extract_packages(
    last_update_date: str | None = None,
    settings: Settings | None = None,
    client: Any | None = None,
) -> Iterator[PackageRecord]:
    """Stream PyPI packages updated after the watermark."""
    from google.cloud.bigquery import QueryJobConfig, ScalarQueryParameter

    cfg = settings or get_settings()
    watermark = parse_watermark(last_update_date or cfg.last_update_date)
    sql = build_extract_sql(cfg)
    bq_client = client or get_bigquery_client(cfg)
    source = _extract_source(cfg)

    job_config = QueryJobConfig(
        query_parameters=[
            ScalarQueryParameter("last_update_date", "STRING", watermark),
        ]
    )

    print(f"[extract] source={source} watermark={watermark}")
    if source == "pypi":
        print(
            f"[extract] SQL primary={cfg.bq_pypi_project}.{cfg.bq_pypi_dataset}"
            f".distribution_metadata"
        )
    else:
        print(f"[extract] SQL source={cfg.bq_source_project}.{cfg.bq_dataset}.projects")
    result = bq_client.query(sql, job_config=job_config)
    yielded = 0
    for row in result:
        payload = dict(row.items()) if hasattr(row, "items") else dict(row)
        yielded += 1
        if yielded == 1:
            print(f"[extract] first row name={payload.get('name')!r}")
        yield row_to_package(payload)
    print(f"[extract] total rows yielded={yielded}")


def count_extract_rows(
    last_update_date: str | None = None,
    settings: Settings | None = None,
    client: Any | None = None,
) -> int:
    """Count rows the ETL would process (cheap diagnostic)."""
    from google.cloud.bigquery import QueryJobConfig, ScalarQueryParameter

    cfg = settings or get_settings()
    watermark = parse_watermark(last_update_date or cfg.last_update_date)
    sql = build_count_sql(cfg)
    bq_client = client or get_bigquery_client(cfg)
    job_config = QueryJobConfig(
        query_parameters=[
            ScalarQueryParameter("last_update_date", "STRING", watermark),
        ]
    )
    rows = list(bq_client.query(sql, job_config=job_config))
    return int(rows[0]["n"]) if rows else 0
