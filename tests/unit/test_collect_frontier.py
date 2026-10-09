"""Frontier: seeding, priority ordering, staleness, dead-lettering, ETag cache."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sarr.collect.frontier import Frontier, SqliteResponseCache

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def _row(name: str, stars: int, sourcerank: int = 0) -> dict[str, object]:
    return {
        "name": name,
        "point_id": f"id-{name}",
        "repo_url": f"https://github.com/o/{name}",
        "stars": stars,
        "sourcerank": sourcerank,
    }


@pytest.fixture
def frontier() -> Frontier:
    return Frontier(":memory:")


@pytest.mark.unit
def test_seed_is_idempotent(frontier: Frontier) -> None:
    assert frontier.seed([_row("a", 1), _row("b", 2)]) == 2
    assert frontier.seed([_row("a", 1), _row("c", 3)]) == 1
    assert frontier.count() == 3


@pytest.mark.unit
def test_never_checked_popular_packages_come_first(frontier: Frontier) -> None:
    frontier.seed([_row("small", 1), _row("big", 50_000), _row("mid", 300)])
    names = [i.name for i in frontier.due(limit=10, stale_days=7, now=NOW)]
    assert names == ["big", "mid", "small"]


@pytest.mark.unit
def test_recently_checked_packages_are_not_due(frontier: Frontier) -> None:
    frontier.seed([_row("fresh", 100), _row("stale", 100)])
    frontier.mark_checked(
        "fresh", checked_at=NOW - timedelta(days=1), health_hash="h", health_status="ok"
    )
    frontier.mark_checked(
        "stale", checked_at=NOW - timedelta(days=30), health_hash="h", health_status="ok"
    )
    assert [i.name for i in frontier.due(limit=10, stale_days=7, now=NOW)] == ["stale"]


@pytest.mark.unit
def test_priority_is_staleness_times_log_popularity(frontier: Frontier) -> None:
    # Popular but recently stale vs obscure but long stale.
    frontier.seed([_row("popular", 100_000), _row("obscure", 2)])
    frontier.mark_checked(
        "popular", checked_at=NOW - timedelta(days=10), health_hash="h", health_status="ok"
    )
    frontier.mark_checked(
        "obscure", checked_at=NOW - timedelta(days=200), health_hash="h", health_status="ok"
    )
    items = frontier.due(limit=10, stale_days=7, now=NOW)
    # 10 * log1p(100000) = 115 vs 200 * log1p(2) = 220
    assert [i.name for i in items] == ["obscure", "popular"]
    assert items[0].priority == pytest.approx(200 * 1.0986, rel=1e-3)


@pytest.mark.unit
def test_failures_dead_letter_after_max(frontier: Frontier) -> None:
    frontier.seed([_row("flaky", 10)])
    assert not frontier.mark_failure("flaky", "boom", max_failures=3)
    assert not frontier.mark_failure("flaky", "boom", max_failures=3)
    assert frontier.mark_failure("flaky", "boom", max_failures=3)
    assert frontier.due(limit=10, stale_days=7, now=NOW) == []
    assert frontier.stats(stale_days=7, now=NOW)["dead_lettered"] == 1


@pytest.mark.unit
def test_success_resets_failures(frontier: Frontier) -> None:
    frontier.seed([_row("flaky", 10)])
    frontier.mark_failure("flaky", "boom", max_failures=3)
    frontier.mark_checked("flaky", checked_at=NOW, health_hash="h", health_status="ok")
    stats = frontier.stats(stale_days=7, now=NOW)
    assert stats["failing"] == 0 and stats["checked"] == 1


@pytest.mark.unit
def test_reseed_keeps_refreshed_stars_and_repo(frontier: Frontier) -> None:
    frontier.seed([_row("a", 10)])
    frontier.mark_checked(
        "a",
        checked_at=NOW,
        health_hash="h",
        health_status="ok",
        stars=99,
        repo_url="https://github.com/new/a",
    )
    frontier.seed([_row("a", 10)])
    row = frontier.conn.execute("SELECT stars, repo_url FROM packages").fetchone()
    assert (row["stars"], row["repo_url"]) == (99, "https://github.com/new/a")


@pytest.mark.unit
def test_stats_and_runs(frontier: Frontier) -> None:
    frontier.seed([_row("a", 10), _row("b", 5)])
    frontier.mark_checked("a", checked_at=NOW, health_hash="h", health_status="gone")
    frontier.record_run(NOW, NOW, {"written": 1})
    stats = frontier.stats(stale_days=7, now=NOW)
    assert stats["total"] == 2
    assert stats["never_checked"] == 1
    assert stats["due"] == 1
    assert stats["gone"] == 1
    assert stats["last_run"]["written"] == 1


@pytest.mark.unit
def test_response_cache_round_trip(frontier: Frontier) -> None:
    cache = SqliteResponseCache(frontier)
    assert cache.get("k") is None
    cache.put("k", '"e1"', {"stars": 1})
    cache.put("k", '"e2"', {"stars": 2})
    assert cache.get("k") == ('"e2"', {"stars": 2})
