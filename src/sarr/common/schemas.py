"""Pydantic schemas shared across API and ETL."""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class PackageRecord(BaseModel):
    """Normalized package row from BigQuery / ETL."""

    name: str
    summary: str | None = None
    keywords: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    classifiers: list[str] = Field(default_factory=list)
    stars: int = 0
    forks: int = 0
    downloads_30d: int | None = None
    last_commit: datetime | date | None = None
    latest_release: datetime | date | None = None
    license: str | None = None
    requires_python: str | None = None
    repo_url: str | None = None
    pypi_url: str | None = None
    update_date: datetime | date | None = None
    content_hash: str | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip().lower().replace("_", "-")


class SearchFilters(BaseModel):
    min_stars: int | None = None
    license: str | None = None
    requires_python: str | None = None
    active_within_days: int | None = Field(
        default=None,
        description="Only packages with last_commit within N days.",
    )


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=512)
    limit: int = Field(default=10, ge=1, le=50)
    rerank: bool | None = None
    filters: SearchFilters | None = None


class SearchHit(BaseModel):
    name: str
    summary: str | None = None
    score: float
    stars: int = 0
    forks: int = 0
    last_commit: datetime | date | None = None
    repo_url: str | None = None
    pypi_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    query: str
    total: int
    reranked: bool
    results: list[SearchHit]
