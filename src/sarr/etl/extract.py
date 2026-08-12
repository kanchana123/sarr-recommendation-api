"""BigQuery extract with watermark filter."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sarr.common.config import Settings, get_settings
from sarr.common.schemas import PackageRecord
from sarr.etl.watermark import parse_watermark

EXTRACT_SQL = """
SELECT
  name,
  summary,
  keywords,
  dependencies,
  classifiers,
  stars,
  forks,
  downloads_30d,
  last_commit,
  latest_release,
  license,
  requires_python,
  repo_url,
  pypi_url,
  update_date
FROM `{project}.{dataset}.{table}`
WHERE update_date > TIMESTAMP(@last_update_date)
ORDER BY update_date ASC
"""


def build_extract_sql(settings: Settings) -> str:
    return EXTRACT_SQL.format(
        project=settings.gcp_project_id,
        dataset=settings.bq_dataset,
        table=settings.bq_table,
    )


def row_to_package(row: dict[str, Any]) -> PackageRecord:
    keywords = row.get("keywords") or []
    dependencies = row.get("dependencies") or []
    classifiers = row.get("classifiers") or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]
    if isinstance(dependencies, str):
        dependencies = [d.strip() for d in dependencies.split(",") if d.strip()]
    if isinstance(classifiers, str):
        classifiers = [c.strip() for c in classifiers.split(",") if c.strip()]

    return PackageRecord(
        name=row["name"],
        summary=row.get("summary"),
        keywords=list(keywords),
        dependencies=list(dependencies),
        classifiers=list(classifiers),
        stars=int(row.get("stars") or 0),
        forks=int(row.get("forks") or 0),
        downloads_30d=row.get("downloads_30d"),
        last_commit=row.get("last_commit"),
        latest_release=row.get("latest_release"),
        license=row.get("license"),
        requires_python=row.get("requires_python"),
        repo_url=row.get("repo_url"),
        pypi_url=row.get("pypi_url"),
        update_date=row.get("update_date"),
    )


def extract_packages(
    last_update_date: str | None = None,
    settings: Settings | None = None,
    client: Any | None = None,
) -> Iterator[PackageRecord]:
    """Stream packages updated after the watermark.

    `client` is injectable for tests; defaults to google.cloud.bigquery.Client.
    """
    cfg = settings or get_settings()
    watermark = parse_watermark(last_update_date or cfg.last_update_date)
    sql = build_extract_sql(cfg)

    if client is None:
        from google.cloud.bigquery import Client

        client = Client(project=cfg.gcp_project_id)

    job_config = None
    try:
        from google.cloud.bigquery import QueryJobConfig, ScalarQueryParameter

        job_config = QueryJobConfig(
            query_parameters=[
                ScalarQueryParameter("last_update_date", "STRING", watermark),
            ]
        )
    except ImportError:
        pass

    result = client.query(sql, job_config=job_config)
    for row in result:
        payload = dict(row.items()) if hasattr(row, "items") else dict(row)
        yield row_to_package(payload)
