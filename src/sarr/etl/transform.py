"""Transform package rows into embeddable docs + Qdrant payloads."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sarr.common.documents import build_search_document
from sarr.common.hashing import content_hash
from sarr.common.schemas import PackageRecord


def _isoformat(value: datetime | date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def to_payload(package: PackageRecord) -> dict[str, Any]:
    return {
        "name": package.name,
        "summary": package.summary,
        "keywords": package.keywords,
        "dependencies": package.dependencies,
        "classifiers": package.classifiers,
        "stars": package.stars,
        "forks": package.forks,
        "downloads_30d": package.downloads_30d,
        "last_commit": _isoformat(package.last_commit),
        "latest_release": _isoformat(package.latest_release),
        "license": package.license,
        "requires_python": package.requires_python,
        "repo_url": package.repo_url,
        "pypi_url": package.pypi_url,
        "update_date": _isoformat(package.update_date),
        "content_hash": package.content_hash or content_hash(package),
    }


def transform_package(package: PackageRecord) -> tuple[str, dict[str, Any], str]:
    """Return (search_document, payload, content_hash)."""
    doc = build_search_document(package)
    digest = content_hash(package)
    enriched = package.model_copy(update={"content_hash": digest})
    return doc, to_payload(enriched), digest
