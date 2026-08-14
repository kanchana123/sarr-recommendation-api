"""Unit tests for the SARR CLI."""

from __future__ import annotations

import json

import pytest

from sarr.cli import build_parser, cmd_search, resolve_api_url
from sarr.common.schemas import SearchHit, SearchRequest, SearchResponse


@pytest.mark.unit
def test_parser_search_defaults() -> None:
    args = build_parser().parse_args(["search", "http client"])
    assert args.query == "http client"
    assert args.limit == 5
    assert args.rerank is False
    assert args.local is False
    assert args.api_url is None


@pytest.mark.unit
def test_resolve_api_url_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SARR_API_URL", raising=False)
    assert resolve_api_url(None) == "http://localhost:8080"
    monkeypatch.setenv("SARR_API_URL", "https://api.example.com/")
    assert resolve_api_url(None) == "https://api.example.com"
    assert resolve_api_url("http://override:9000") == "http://override:9000"


@pytest.mark.unit
def test_cmd_search_uses_default_api(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_api(request: SearchRequest, api_url: str) -> SearchResponse:
        assert api_url == "http://localhost:8080"
        assert request.query == "ml"
        return SearchResponse(
            query=request.query,
            total=1,
            reranked=False,
            took_ms=12.0,
            results=[
                SearchHit(
                    name="scikit-learn",
                    summary="ML in Python",
                    score=0.91,
                    stars=50000,
                )
            ],
        )

    monkeypatch.delenv("SARR_API_URL", raising=False)
    monkeypatch.setattr("sarr.cli._search_via_api", fake_api)
    args = build_parser().parse_args(["search", "ml", "--json"])
    assert cmd_search(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"][0]["name"] == "scikit-learn"
