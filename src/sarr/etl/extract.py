"""BigQuery extract via google.cloud.bigquery.Client.

MVP source: Libraries.io public dataset (PyPI projects + repo stars).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from sarr.common.config import Settings, get_settings
from sarr.common.schemas import PackageRecord
from sarr.etl.watermark import parse_watermark

# Your proven Libraries.io query + watermark on latest release time.
EXTRACT_SQL = """
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


def build_extract_sql(settings: Settings) -> str:
    return EXTRACT_SQL.format(
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


def row_to_package(row: dict[str, Any]) -> PackageRecord:
    name = str(row["name"]).strip().lower().replace("_", "-")
    release_ts = _as_datetime(
        row.get("latest_release_publish_timestamp") or row.get("update_date")
    )
    return PackageRecord(
        name=name,
        summary=row.get("description") or row.get("summary"),
        keywords=_as_list(row.get("keywords")),
        dependencies=_as_list(row.get("dependencies")),
        classifiers=_as_list(row.get("classifiers")),
        stars=int(row.get("repository_stars_count") or row.get("stars") or 0),
        forks=int(row.get("forks") or 0),
        downloads_30d=row.get("downloads_30d"),
        last_commit=row.get("last_commit"),
        latest_release=release_ts,
        license=_as_license(row.get("licenses") or row.get("license")),
        requires_python=row.get("requires_python"),
        repo_url=row.get("repository_url") or row.get("repo_url"),
        homepage_url=row.get("homepage_url"),
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
    """Stream PyPI packages from Libraries.io updated after the watermark."""
    from google.cloud.bigquery import QueryJobConfig, ScalarQueryParameter

    cfg = settings or get_settings()
    watermark = parse_watermark(last_update_date or cfg.last_update_date)
    sql = build_extract_sql(cfg)
    bq_client = client or get_bigquery_client(cfg)

    job_config = QueryJobConfig(
        query_parameters=[
            ScalarQueryParameter("last_update_date", "STRING", watermark),
        ]
    )

    print(f"[extract] watermark={watermark}")
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
    sql = f"""
    SELECT COUNT(*) AS n
    FROM `{cfg.bq_source_project}.{cfg.bq_dataset}.projects` AS p
    WHERE p.platform = 'Pypi'
      AND p.description IS NOT NULL
      AND TRIM(p.description) != ''
      AND LENGTH(p.description) > 5
      AND p.latest_release_publish_timestamp IS NOT NULL
      AND p.latest_release_publish_timestamp > TIMESTAMP(@last_update_date)
    """
    bq_client = client or get_bigquery_client(cfg)
    job_config = QueryJobConfig(
        query_parameters=[
            ScalarQueryParameter("last_update_date", "STRING", watermark),
        ]
    )
    rows = list(bq_client.query(sql, job_config=job_config))
    return int(rows[0]["n"]) if rows else 0
