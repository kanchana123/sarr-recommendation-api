"""GraphQL schema over the same cosine ANN search path as POST /v1/search."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any

import strawberry
from starlette.concurrency import run_in_threadpool
from strawberry.extensions import MaxAliasesLimiter, QueryDepthLimiter
from strawberry.fastapi import GraphQLRouter

from sarr.api import routes
from sarr.common.schemas import SearchFilters, SearchHit, SearchRequest, SearchResponse

logger = logging.getLogger("sarr.api")

MAX_QUERY_DEPTH = 6
MAX_ALIASES = 5


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


@strawberry.type(description="Per-stage server timings in milliseconds.")
class Timing:
    embed_ms: float | None = None
    qdrant_ms: float | None = None
    rerank_ms: float | None = None
    blend_ms: float | None = None


@strawberry.type(description="A PyPI package returned by semantic search.")
class Package:
    name: str
    summary: str | None
    score: float = strawberry.field(description="Blended relevance + popularity + recency.")
    stars: int
    forks: int
    last_commit: str | None = strawberry.field(description="ISO-8601 date of the last commit.")
    latest_release: str | None
    repo_url: str | None
    pypi_url: str | None
    homepage_url: str | None
    license: str | None
    requires_python: str | None
    keywords: list[str]
    downloads_30d: int | None
    dependent_projects_count: int | None
    sourcerank: int | None

    @classmethod
    def from_hit(cls, hit: SearchHit) -> Package:
        meta = hit.metadata
        return cls(
            name=hit.name,
            summary=hit.summary,
            score=hit.score,
            stars=hit.stars,
            forks=hit.forks,
            last_commit=_iso(hit.last_commit),
            latest_release=_iso(meta.get("latest_release")),
            repo_url=hit.repo_url,
            pypi_url=hit.pypi_url,
            homepage_url=meta.get("homepage_url"),
            license=meta.get("license"),
            requires_python=meta.get("requires_python"),
            keywords=_str_list(meta.get("keywords")),
            downloads_30d=_int_or_none(meta.get("downloads_30d")),
            dependent_projects_count=_int_or_none(meta.get("dependent_projects_count")),
            sourcerank=_int_or_none(meta.get("sourcerank")),
        )


@strawberry.type(description="Ranked search results with server latency.")
class SearchResult:
    query: str
    total: int
    reranked: bool
    took_ms: float | None = strawberry.field(description="Server-side search latency.")
    timing: Timing | None
    packages: list[Package]

    @classmethod
    def from_response(cls, response: SearchResponse) -> SearchResult:
        timing = response.timing_ms
        return cls(
            query=response.query,
            total=response.total,
            reranked=response.reranked,
            took_ms=response.took_ms,
            timing=Timing(
                embed_ms=timing.get("embed_ms"),
                qdrant_ms=timing.get("qdrant_ms"),
                rerank_ms=timing.get("rerank_ms"),
                blend_ms=timing.get("blend_ms"),
            )
            if timing
            else None,
            packages=[Package.from_hit(hit) for hit in response.results],
        )


@strawberry.input(description="Optional payload filters applied inside the ANN search.")
class SearchFiltersInput:
    min_stars: int | None = None
    license: str | None = None
    requires_python: str | None = None


@strawberry.type
class Query:
    @strawberry.field(description="Liveness check; mirrors GET /healthz.")
    def health(self) -> str:
        return "ok"

    @strawberry.field(
        description=(
            "Semantic package search: query embed, cosine ANN in Qdrant, "
            "optional cross-encoder rerank, score blend."
        )
    )
    async def search(
        self,
        query: str,
        limit: int = 10,
        rerank: bool | None = None,
        filters: SearchFiltersInput | None = None,
    ) -> SearchResult:
        request = SearchRequest(
            query=query,
            limit=limit,
            rerank=rerank,
            filters=SearchFilters(
                min_stars=filters.min_stars,
                license=filters.license,
                requires_python=filters.requires_python,
            )
            if filters
            else None,
        )
        started = time.perf_counter()
        # Search is CPU-bound (ONNX embed/rerank); keep it off the event loop.
        response = await run_in_threadpool(routes.get_search_service().search, request)
        response.took_ms = round((time.perf_counter() - started) * 1000.0, 1)
        logger.info(
            "graphql search ok query=%r limit=%s rerank=%s total=%s took_ms=%.1f",
            request.query,
            request.limit,
            response.reranked,
            response.total,
            response.took_ms,
        )
        return SearchResult.from_response(response)


schema = strawberry.Schema(
    query=Query,
    extensions=[
        lambda: QueryDepthLimiter(max_depth=MAX_QUERY_DEPTH),
        lambda: MaxAliasesLimiter(max_alias_count=MAX_ALIASES),
    ],
)


def create_graphql_router() -> GraphQLRouter[Any, Any]:
    return GraphQLRouter(schema, path="/graphql")
