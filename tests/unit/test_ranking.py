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


@pytest.mark.unit
def test_health_term_is_off_by_default() -> None:
    payload = {"stars": 100}
    assert blend_scores(0.5, payload) == blend_scores(0.5, {**payload, "health_score": 1.0})


@pytest.mark.unit
def test_health_term_rewards_healthy_packages_when_enabled() -> None:
    healthy = blend_scores(0.5, {"stars": 100, "health_score": 0.9}, epsilon=0.05)
    unhealthy = blend_scores(0.5, {"stars": 100, "health_score": 0.1}, epsilon=0.05)
    unscored = blend_scores(0.5, {"stars": 100}, epsilon=0.05)
    assert healthy > unscored > unhealthy
    base = blend_scores(0.5, {"stars": 100})
    assert unscored == pytest.approx(0.95 * base + 0.05 * 0.5)
