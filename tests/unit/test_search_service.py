"""Unit tests for search service with mocked dependencies."""

from __future__ import annotations

import pytest

from sarr.api.search_service import SearchService
from sarr.common.config import Settings
from sarr.common.schemas import SearchFilters, SearchRequest


class FakeEmbedder:
    def embed(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]


class FakeStore:
    def search(self, vector, *, limit: int, query_filter=None, vectors_only=None):
        return [
            {
                "id": "requests",
                "score": 0.91,
                "payload": {"name": "requests"},
            },
            {
                "id": "httpx",
                "score": 0.88,
                "payload": {"name": "httpx"},
            },
        ][:limit]


class FakeReranker:
    def rerank(self, query: str, documents: list[str]) -> list[float]:
        # Prefer the second candidate
        return [0.1, 0.9][: len(documents)]


class FakeMetadataStore:
    def fetch_by_names(self, names: list[str]):
        return {
            name: {
                "name": name,
                "summary": "HTTP for Humans" if name == "requests" else "HTTP client",
                "stars": 52000 if name == "requests" else 12000,
                "forks": 100 if name == "requests" else 50,
                "last_commit": "2024-01-01T00:00:00+00:00",
                "pypi_url": f"https://pypi.org/project/{name}/",
            }
            for name in names
        }


@pytest.mark.unit
def test_search_output_limit_caps_results() -> None:
    class ManyHitStore:
        def search(self, vector, *, limit: int, query_filter=None, vectors_only=None):
            hits = [
                {
                    "id": f"pkg-{i}",
                    "score": 1.0 - i * 0.01,
                    "payload": {"name": f"pkg-{i}", "summary": f"Package {i}", "stars": 1},
                }
                for i in range(20)
            ]
            return hits[:limit]

    service = SearchService(
        settings=Settings(
            rerank_enabled_default=False,
            search_top_k=50,
            rerank_top_k=50,
            qdrant_vectors_only=False,
        ),
        embedder=FakeEmbedder(),  # type: ignore[arg-type]
        vector_store=ManyHitStore(),  # type: ignore[arg-type]
        reranker=FakeReranker(),  # type: ignore[arg-type]
        warm=False,
    )
    response = service.search(
        SearchRequest(query="http client", limit=50, rerank=False),
        output_limit=10,
    )
    assert response.total == 10
    assert len(response.results) == 10
    assert response.results[-1].name == "pkg-9"


@pytest.mark.unit
def test_search_without_rerank_returns_hits() -> None:
    service = SearchService(
        settings=Settings(
            rerank_enabled_default=False,
            search_top_k=10,
            rerank_top_k=10,
            qdrant_vectors_only=True,
        ),
        embedder=FakeEmbedder(),  # type: ignore[arg-type]
        vector_store=FakeStore(),  # type: ignore[arg-type]
        reranker=FakeReranker(),  # type: ignore[arg-type]
        metadata_store=FakeMetadataStore(),  # type: ignore[arg-type]
        warm=False,
    )
    response = service.search(SearchRequest(query="http client", limit=2, rerank=False))
    assert response.total == 2
    assert response.reranked is False
    assert response.results[0].name in {"requests", "httpx"}
    assert response.timing_ms is not None
    assert "embed_ms" in response.timing_ms
    assert "qdrant_ms" in response.timing_ms
    assert "bq_ms" in response.timing_ms


@pytest.mark.unit
def test_rerank_runs_after_bigquery_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []

    class TrackingReranker(FakeReranker):
        def rerank(self, query: str, documents: list[str]) -> list[float]:
            seen.append(all("HTTP" in doc for doc in documents))
            return super().rerank(query, documents)

    service = SearchService(
        settings=Settings(
            rerank_enabled_default=False,
            search_top_k=10,
            rerank_top_k=10,
            qdrant_vectors_only=True,
        ),
        embedder=FakeEmbedder(),  # type: ignore[arg-type]
        vector_store=FakeStore(),  # type: ignore[arg-type]
        reranker=TrackingReranker(),  # type: ignore[arg-type]
        metadata_store=FakeMetadataStore(),  # type: ignore[arg-type]
        warm=False,
    )
    service.search(SearchRequest(query="http client", limit=2, rerank=True))
    assert seen == [True]


@pytest.mark.unit
def test_search_with_rerank_reorders() -> None:
    service = SearchService(
        settings=Settings(
            rerank_enabled_default=False,
            search_top_k=10,
            rerank_top_k=10,
            qdrant_vectors_only=True,
        ),
        embedder=FakeEmbedder(),  # type: ignore[arg-type]
        vector_store=FakeStore(),  # type: ignore[arg-type]
        reranker=FakeReranker(),  # type: ignore[arg-type]
        metadata_store=FakeMetadataStore(),  # type: ignore[arg-type]
        warm=False,
    )
    response = service.search(SearchRequest(query="http client", limit=2, rerank=True))
    assert response.reranked is True
    assert response.results[0].name == "httpx"


@pytest.mark.unit
def test_bigquery_hydrate_failure_raises() -> None:
    class FailingMetadata:
        def fetch_by_names(self, names: list[str]) -> dict[str, dict[str, object]]:
            raise RuntimeError("syntax error")

    service = SearchService(
        settings=Settings(qdrant_vectors_only=True, search_top_k=10, rerank_top_k=10),
        embedder=FakeEmbedder(),  # type: ignore[arg-type]
        vector_store=FakeStore(),  # type: ignore[arg-type]
        reranker=FakeReranker(),  # type: ignore[arg-type]
        metadata_store=FailingMetadata(),  # type: ignore[arg-type]
        warm=False,
    )
    with pytest.raises(RuntimeError, match="BigQuery metadata hydrate failed"):
        service.search(SearchRequest(query="http client", limit=2))


@pytest.mark.unit
def test_post_hydrate_min_stars_filter() -> None:
    class FilteredMetadata(FakeMetadataStore):
        def fetch_by_names(self, names: list[str]):
            payloads = super().fetch_by_names(names)
            payloads["httpx"] = {**payloads["httpx"], "stars": 5}
            return payloads

    service = SearchService(
        settings=Settings(
            qdrant_vectors_only=True,
            search_top_k=10,
            rerank_top_k=10,
            search_metadata_over_fetch=1,
        ),
        embedder=FakeEmbedder(),  # type: ignore[arg-type]
        vector_store=FakeStore(),  # type: ignore[arg-type]
        reranker=FakeReranker(),  # type: ignore[arg-type]
        metadata_store=FilteredMetadata(),  # type: ignore[arg-type]
        warm=False,
    )
    response = service.search(
        SearchRequest(
            query="http client",
            limit=5,
            rerank=False,
            filters=SearchFilters(min_stars=1000),
        )
    )
    assert response.total == 1
    assert response.results[0].name == "requests"


@pytest.mark.unit
def test_skips_hits_missing_bigquery_metadata() -> None:
    class PartialMetadata(FakeMetadataStore):
        def fetch_by_names(self, names: list[str]):
            return super().fetch_by_names(["requests"])

    service = SearchService(
        settings=Settings(qdrant_vectors_only=True, search_top_k=10, rerank_top_k=10),
        embedder=FakeEmbedder(),  # type: ignore[arg-type]
        vector_store=FakeStore(),  # type: ignore[arg-type]
        reranker=FakeReranker(),  # type: ignore[arg-type]
        metadata_store=PartialMetadata(),  # type: ignore[arg-type]
        warm=False,
    )
    response = service.search(SearchRequest(query="http client", limit=5, rerank=False))
    assert response.total == 1
    assert response.results[0].name == "requests"


@pytest.mark.unit
def test_legacy_mode_uses_qdrant_payload_without_bigquery() -> None:
    class LegacyStore:
        def search(self, vector, *, limit: int, query_filter=None, vectors_only=None):
            return [
                {
                    "id": "1",
                    "score": 0.9,
                    "payload": {
                        "name": "requests",
                        "summary": "HTTP for Humans.",
                        "stars": 100,
                    },
                }
            ]

    class ShouldNotRunMetadata:
        def fetch_by_names(self, names: list[str]) -> dict[str, dict[str, object]]:
            raise AssertionError("BQ should not be called when summary exists")

    service = SearchService(
        settings=Settings(qdrant_vectors_only=False, search_top_k=10, rerank_top_k=10),
        embedder=FakeEmbedder(),  # type: ignore[arg-type]
        vector_store=LegacyStore(),  # type: ignore[arg-type]
        reranker=FakeReranker(),  # type: ignore[arg-type]
        metadata_store=ShouldNotRunMetadata(),  # type: ignore[arg-type]
        warm=False,
    )
    response = service.search(SearchRequest(query="http", limit=1, rerank=False))
    assert response.total == 1
    assert response.results[0].summary == "HTTP for Humans."
