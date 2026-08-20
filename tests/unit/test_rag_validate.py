"""Citation guardrail for grounded RAG recommendations."""

from __future__ import annotations

import pytest

from sarr.api.rag_prompt import build_prompt
from sarr.api.rag_validate import validate_recommendations
from sarr.common.schemas import SearchHit


def _hits() -> list[SearchHit]:
    return [
        SearchHit(name="requests", summary="HTTP for Humans.", score=0.9),
        SearchHit(name="httpx", summary="A next-generation HTTP client.", score=0.8),
        SearchHit(name="aiohttp", summary="Async HTTP client/server.", score=0.7),
    ]


@pytest.mark.unit
def test_prompt_includes_only_name_and_description() -> None:
    prompt = build_prompt("async HTTP client", _hits())
    assert "User query:\nasync HTTP client" in prompt
    assert "name: requests" in prompt
    assert "description: HTTP for Humans." in prompt
    assert "stars" not in prompt
    assert "pypi_url" not in prompt


@pytest.mark.unit
def test_drops_packages_not_in_retrieved_set() -> None:
    raw = """
    {
      "recommendations": [
        {
          "package": "httpx",
          "reason": "Modern HTTP client for async work.",
          "cited_snippet": "next-generation HTTP client"
        },
        {
          "package": "made-up-lib",
          "reason": "Does not exist.",
          "cited_snippet": "invented"
        },
        {
          "package": "Requests",
          "reason": "Widely used HTTP library.",
          "cited_snippet": "HTTP for Humans"
        }
      ]
    }
    """
    result = validate_recommendations(raw, _hits())
    assert [rec.package for rec in result.recommendations] == ["httpx", "requests"]
    assert result.dropped == ["made-up-lib"]
    assert result.parse_error is None
    assert result.recommendations[0].snippet_in_description is True


@pytest.mark.unit
def test_parse_error_does_not_invent_packages() -> None:
    result = validate_recommendations("not json", _hits())
    assert result.recommendations == []
    assert result.parse_error is not None
