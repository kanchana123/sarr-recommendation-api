"""Unit tests for ranking blend."""

from datetime import UTC, datetime, timedelta

import pytest

from sarr.api.ranking import blend_scores


@pytest.mark.unit
def test_blend_scores_prefers_higher_relevance() -> None:
    payload = {"stars": 100, "last_commit": datetime.now(UTC).isoformat()}
    low = blend_scores(0.2, payload)
    high = blend_scores(0.9, payload)
    assert high > low


@pytest.mark.unit
def test_blend_scores_boosts_popularity() -> None:
    now = datetime.now(UTC).isoformat()
    unpopular = blend_scores(0.8, {"stars": 1, "last_commit": now})
    popular = blend_scores(0.8, {"stars": 50_000, "last_commit": now})
    assert popular > unpopular


@pytest.mark.unit
def test_blend_scores_boosts_recent_commits() -> None:
    recent = blend_scores(
        0.8,
        {"stars": 100, "last_commit": datetime.now(UTC).isoformat()},
    )
    stale = blend_scores(
        0.8,
        {
            "stars": 100,
            "last_commit": (datetime.now(UTC) - timedelta(days=800)).isoformat(),
        },
    )
    assert recent > stale
