"""Unit tests for shared API request/response schemas."""

from __future__ import annotations

import pytest

from sarr.common.schemas import SearchRequest


@pytest.mark.unit
def test_search_request_default_limit_is_ten() -> None:
    assert SearchRequest(query="async HTTP").limit == 10
