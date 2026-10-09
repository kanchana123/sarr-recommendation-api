"""GraphQL endpoint tests against a stubbed search service."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sarr.api import routes
from sarr.api.app import create_app
from sarr.common.schemas import SearchHit, SearchRequest, SearchResponse


class StubSearchService:
    def __init__(self) -> None:
        self.requests: list[SearchRequest] = []

    def search(self, request: SearchRequest) -> SearchResponse:
        self.requests.append(request)
        return SearchResponse(
            query=request.query,
            total=1,
            reranked=bool(request.rerank),
            results=[
                SearchHit(
                    name="requests",
                    summary="HTTP for Humans",
                    score=0.95,
                    stars=52000,
                    forks=9400,
                    last_commit=date(2024, 5, 1),
                    repo_url="https://github.com/psf/requests",
                    pypi_url="https://pypi.org/project/requests/",
                    metadata={
                        "license": "Apache-2.0",
                        "requires_python": ">=3.8",
                        "keywords": ["http", "client"],
                        "downloads_30d": "1000",
                        "sourcerank": 30,
                    },
                )
            ],
            timing_ms={"embed_ms": 1.0, "qdrant_ms": 2.0, "rerank_ms": 0.0, "blend_ms": 0.1},
        )


class FailingSearchService:
    def search(self, request: SearchRequest) -> SearchResponse:
        raise RuntimeError("qdrant unavailable")


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> StubSearchService:
    service = StubSearchService()
    monkeypatch.setattr(routes, "_service", service)
    return service


@pytest.fixture
def client(stub: StubSearchService) -> TestClient:
    return TestClient(create_app())


def _gql(client: TestClient, query: str, variables: dict[str, Any] | None = None) -> Any:
    response = client.post("/graphql", json={"query": query, "variables": variables or {}})
    assert response.status_code == 200
    return response.json()


@pytest.mark.unit
def test_health_query(client: TestClient) -> None:
    body = _gql(client, "{ health }")
    assert body == {"data": {"health": "ok"}}


@pytest.mark.unit
def test_search_returns_only_selected_fields(client: TestClient, stub: StubSearchService) -> None:
    body = _gql(client, '{ search(query: "http library", limit: 5) { packages { name stars } } }')
    assert "errors" not in body
    assert body["data"]["search"]["packages"] == [{"name": "requests", "stars": 52000}]
    assert stub.requests[0].query == "http library"
    assert stub.requests[0].limit == 5


@pytest.mark.unit
def test_search_maps_payload_metadata_and_timing(client: TestClient) -> None:
    query = """
      query Search($q: String!) {
        search(query: $q, rerank: true) {
          query total reranked tookMs
          timing { embedMs qdrantMs rerankMs blendMs }
          packages {
            name score lastCommit repoUrl license requiresPython
            keywords downloads30d sourcerank latestRelease
          }
        }
      }
    """
    body = _gql(client, query, {"q": "http client"})
    assert "errors" not in body
    result = body["data"]["search"]
    assert result["query"] == "http client"
    assert result["reranked"] is True
    assert isinstance(result["tookMs"], float)
    assert result["timing"] == {"embedMs": 1.0, "qdrantMs": 2.0, "rerankMs": 0.0, "blendMs": 0.1}
    pkg = result["packages"][0]
    assert pkg["lastCommit"] == "2024-05-01"
    assert pkg["license"] == "Apache-2.0"
    assert pkg["requiresPython"] == ">=3.8"
    assert pkg["keywords"] == ["http", "client"]
    assert pkg["downloads30d"] == 1000
    assert pkg["sourcerank"] == 30
    assert pkg["latestRelease"] is None


@pytest.mark.unit
def test_search_passes_filters_to_service(client: TestClient, stub: StubSearchService) -> None:
    body = _gql(
        client,
        '{ search(query: "orm", filters: {minStars: 100, license: "MIT"}) { total } }',
    )
    assert "errors" not in body
    filters = stub.requests[0].filters
    assert filters is not None
    assert filters.min_stars == 100
    assert filters.license == "MIT"
    assert filters.requires_python is None


@pytest.mark.unit
def test_search_rejects_out_of_range_limit(client: TestClient, stub: StubSearchService) -> None:
    body = _gql(client, '{ search(query: "orm", limit: 500) { total } }')
    assert body["data"] is None
    assert "limit" in body["errors"][0]["message"]
    assert stub.requests == []


@pytest.mark.unit
def test_search_rejects_empty_query(client: TestClient, stub: StubSearchService) -> None:
    body = _gql(client, '{ search(query: "") { total } }')
    assert body["data"] is None
    assert body["errors"]
    assert stub.requests == []


@pytest.mark.unit
def test_search_failure_surfaces_as_graphql_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "_service", FailingSearchService())
    body = _gql(TestClient(create_app()), '{ search(query: "orm") { total } }')
    assert body["data"] is None
    assert "qdrant unavailable" in body["errors"][0]["message"]


@pytest.mark.unit
def test_alias_limit_blocks_search_fan_out(client: TestClient, stub: StubSearchService) -> None:
    aliases = " ".join(f'a{i}: search(query: "q{i}") {{ total }}' for i in range(10))
    body = _gql(client, f"{{ {aliases} }}")
    assert body["errors"]
    assert stub.requests == []


@pytest.mark.unit
def test_unknown_field_is_rejected(client: TestClient, stub: StubSearchService) -> None:
    body = _gql(client, '{ search(query: "orm") { packages { notAField } } }')
    assert body["errors"]
    assert stub.requests == []


@pytest.mark.unit
def test_rest_search_still_works(client: TestClient) -> None:
    response = client.post("/v1/search", json={"query": "http library"})
    assert response.status_code == 200
    assert response.json()["results"][0]["name"] == "requests"


@pytest.mark.unit
def test_search_exposes_health_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    class HealthStub(StubSearchService):
        def search(self, request: SearchRequest) -> SearchResponse:
            response = super().search(request)
            response.results[0].metadata.update(
                health_score=0.87,
                health_status="ok",
                health_checked_at="2026-10-09T12:00:00+00:00",
                open_issues_count=200,
                release_cadence_days=31.5,
                commits_90d=5,
            )
            return response

    monkeypatch.setattr(routes, "_service", HealthStub())
    query = """{ search(query: "http") { packages {
        healthScore healthStatus healthCheckedAt openIssuesCount releaseCadenceDays commits90d
    } } }"""
    body = _gql(TestClient(create_app()), query)
    assert "errors" not in body
    assert body["data"]["search"]["packages"][0] == {
        "healthScore": 0.87,
        "healthStatus": "ok",
        "healthCheckedAt": "2026-10-09T12:00:00+00:00",
        "openIssuesCount": 200,
        "releaseCadenceDays": 31.5,
        "commits90d": 5,
    }


@pytest.mark.unit
def test_health_fields_are_null_before_collection(client: TestClient) -> None:
    body = _gql(client, '{ search(query: "http") { packages { healthScore healthStatus } } }')
    assert body["data"]["search"]["packages"][0] == {"healthScore": None, "healthStatus": None}
