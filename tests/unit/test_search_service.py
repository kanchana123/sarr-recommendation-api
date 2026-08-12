"""Unit tests for search service with mocked dependencies."""

from __future__ import annotations

import pytest

from sarr.api.search_service import SearchService
from sarr.common.config import Settings
from sarr.common.schemas import SearchRequest


class FakeEmbedder:
    def embed(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]


class FakeStore:
    def search(self, vector, *, limit: int, query_filter=None):
        return [
            {
                "id": "requests",
                "score": 0.91,
                "payload": {
                    "name": "requests",
                    "summary": "HTTP for Humans",
                    "stars": 52000,
                    "forks": 100,
                    "last_commit": "2024-01-01T00:00:00+00:00",
                    "pypi_url": "https://pypi.org/project/requests/",
                },
            },
            {
                "id": "httpx",
                "score": 0.88,
                "payload": {
                    "name": "httpx",
                    "summary": "HTTP client",
                    "stars": 12000,
                    "forks": 50,
                    "last_commit": "2024-06-01T00:00:00+00:00",
                },
            },
        ][:limit]


class FakeReranker:
    def rerank(self, query: str, documents: list[str]) -> list[float]:
        # Prefer the second candidate
        return [0.1, 0.9][: len(documents)]


@pytest.mark.unit
def test_search_without_rerank_returns_hits() -> None:
    service = SearchService(
        settings=Settings(rerank_enabled_default=False, search_top_k=10, rerank_top_k=10),
        embedder=FakeEmbedder(),  # type: ignore[arg-type]
        vector_store=FakeStore(),  # type: ignore[arg-type]
        reranker=FakeReranker(),  # type: ignore[arg-type]
    )
    response = service.search(SearchRequest(query="http client", limit=2, rerank=False))
    assert response.total == 2
    assert response.reranked is False
    assert response.results[0].name in {"requests", "httpx"}


@pytest.mark.unit
def test_search_with_rerank_reorders() -> None:
    service = SearchService(
        settings=Settings(rerank_enabled_default=False, search_top_k=10, rerank_top_k=10),
        embedder=FakeEmbedder(),  # type: ignore[arg-type]
        vector_store=FakeStore(),  # type: ignore[arg-type]
        reranker=FakeReranker(),  # type: ignore[arg-type]
    )
    response = service.search(SearchRequest(query="http client", limit=2, rerank=True))
    assert response.reranked is True
    assert response.results[0].name == "httpx"
