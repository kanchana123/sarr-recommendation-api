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
    latest_release: datetime | date | str | None = None
    license: str | None = None
    requires_python: str | None = None
    repo_url: str | None = None
    homepage_url: str | None = None
    pypi_url: str | None = None
    update_date: datetime | date | str | None = None
    content_hash: str | None = None
    # Libraries.io ranking signals
    sourcerank: int = 0
    dependent_projects_count: int = 0
    versions_count: int = 0
    dependent_repositories_count: int = 0
    language: str | None = None
    platform: str | None = "Pypi"

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
    took_ms: float | None = Field(
        default=None,
        description="Server-side search latency in milliseconds.",
    )
    timing_ms: dict[str, float] | None = Field(
        default=None,
        description="Per-stage server timings in milliseconds.",
    )


class RagRequest(BaseModel):
    query: str = Field(min_length=1, max_length=512)
    filters: SearchFilters | None = None


class LlmRecommendation(BaseModel):
    package: str
    reason: str = Field(min_length=1, max_length=800)
    cited_snippet: str = Field(min_length=1, max_length=400)


class LlmRecommendationList(BaseModel):
    recommendations: list[LlmRecommendation] = Field(min_length=1, max_length=3)


class GroundedRecommendation(LlmRecommendation):
    snippet_in_description: bool = False


class RagLlmResult(BaseModel):
    recommendations: list[GroundedRecommendation]
    dropped: list[str] = Field(default_factory=list)
    parse_error: str | None = None
