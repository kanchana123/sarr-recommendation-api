"""Config validation helpers for ETL / Colab."""

from __future__ import annotations

from sarr.common.config import Settings

_PLACEHOLDERS = (
    "your-gcp-project-id",
    "YOUR_GCP_PROJECT",
    "YOUR-CLUSTER",
    "YOUR_QDRANT_KEY",
    "YOUR_QDRANT",
)


def validate_etl_settings(settings: Settings) -> list[str]:
    """Return human-readable config problems (empty list means OK)."""
    problems: list[str] = []

    if not settings.gcp_project_id or settings.gcp_project_id in _PLACEHOLDERS:
        problems.append(
            "GCP_PROJECT_ID must be your real GCP billing project "
            "(not 'your-gcp-project-id' and not 'bigquery-public-data')."
        )
    if settings.gcp_project_id == "bigquery-public-data":
        problems.append(
            "GCP_PROJECT_ID cannot be 'bigquery-public-data' — that project is "
            "read-only public data. Use your own GCP project for billing."
        )
    if not settings.qdrant_url or any(p in settings.qdrant_url for p in _PLACEHOLDERS):
        problems.append("QDRANT_URL still looks like a placeholder.")
    if not settings.qdrant_api_key or any(
        p in (settings.qdrant_api_key or "") for p in _PLACEHOLDERS
    ):
        problems.append("QDRANT_API_KEY is missing or still a placeholder.")
    if not settings.qdrant_collection:
        problems.append("QDRANT_COLLECTION is empty.")
    return problems
