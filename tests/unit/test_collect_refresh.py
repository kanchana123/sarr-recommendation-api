"""End-to-end refresh against mocked GitHub/PyPI and an in-memory payload store."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from collect_fakes import FakeWeb, MemoryStore, make_clients, make_store

from sarr.collect.cli import main as cli_main
from sarr.collect.frontier import Frontier, SqliteResponseCache
from sarr.collect.refresh import backfill, run_refresh

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
_store = make_store


def _refresh(frontier: Frontier, web: FakeWeb, store: MemoryStore, **kwargs: Any) -> Any:
    github, pypi = make_clients(web, SqliteResponseCache(frontier))
    options: dict[str, Any] = {"limit": 100, "stale_days": 7, "max_failures": 3, "now": lambda: NOW}
    options.update(kwargs)
    return run_refresh(frontier, github, pypi, store, **options)


@pytest.mark.unit
def test_backfill_keeps_top_n_github_packages() -> None:
    frontier = Frontier(":memory:")
    result = backfill(frontier, _store(), top_n=2)
    assert result == {
        "scanned": 5,
        "github_linked": 3,
        "selected": 2,
        "added": 2,
        "frontier_total": 2,
    }
    names = [i.name for i in frontier.due(limit=10, stale_days=7, now=NOW)]
    assert names == ["requests", "httpx"]


@pytest.mark.unit
def test_refresh_writes_partial_payloads() -> None:
    frontier, store, web = Frontier(":memory:"), _store(), FakeWeb()
    backfill(frontier, store, top_n=10)
    stats = _refresh(frontier, web, store)

    assert (stats.selected, stats.processed, stats.written, stats.gone) == (3, 3, 3, 1)
    requests_payload = store.points["p-requests"]
    assert requests_payload["stars"] == 1234
    assert requests_payload["downloads_30d"] == 42
    assert requests_payload["requires_python"] == ">=3.10"
    assert requests_payload["release_cadence_days"] == 31.0
    assert requests_payload["health_status"] == "ok"
    assert requests_payload["health_checked_at"] == "2026-10-09T12:00:00+00:00"
    # Existing fields not touched by the collector survive the partial update.
    assert requests_payload["sourcerank"] == 1
    assert store.points["p-ghost"]["health_status"] == "gone"
    assert store.points["p-ghost"]["health_score"] == 0.0


@pytest.mark.unit
def test_immediate_rerun_does_nothing() -> None:
    frontier, store, web = Frontier(":memory:"), _store(), FakeWeb()
    backfill(frontier, store, top_n=10)
    _refresh(frontier, web, store)
    writes, requests = len(store.writes), len(web.requests)

    stats = _refresh(frontier, web, store)
    assert stats.selected == 0
    assert len(store.writes) == writes and len(web.requests) == requests


@pytest.mark.unit
def test_forced_recheck_dedups_and_uses_etags() -> None:
    frontier, store, web = Frontier(":memory:"), _store(), FakeWeb()
    backfill(frontier, store, top_n=10)
    _refresh(frontier, web, store)
    store.writes.clear()

    later = NOW + timedelta(days=8)
    stats = _refresh(frontier, web, store, now=lambda: later)
    assert (stats.selected, stats.written, stats.unchanged) == (3, 0, 3)
    # Unchanged packages only get a fresh timestamp, not a full payload rewrite.
    assert all(set(payload) == {"health_checked_at"} for _, payload in store.writes)
    rechecks = web.requests[-7:]
    conditional = [r for r in rechecks if "If-None-Match" in r.headers]
    assert conditional, "rechecks should send If-None-Match"


@pytest.mark.unit
def test_failures_are_retried_then_dead_lettered() -> None:
    frontier, store, web = Frontier(":memory:"), _store(), FakeWeb()
    web.broken.add("httpx")
    backfill(frontier, store, top_n=10)

    for attempt in range(3):
        stats = _refresh(frontier, web, store)
        assert stats.failed == 1
        assert stats.dead_lettered == (1 if attempt == 2 else 0)

    assert store.points["p-httpx"]["health_status"] == "error"
    assert frontier.stats(stale_days=7, now=NOW)["dead_lettered"] == 1


@pytest.mark.unit
def test_max_hours_budget_stops_the_run() -> None:
    frontier, store, web = Frontier(":memory:"), _store(), FakeWeb()
    backfill(frontier, store, top_n=10)
    ticks = iter([0.0, 0.0, 10_000.0, 10_000.0, 10_000.0])
    stats = _refresh(frontier, web, store, max_hours=1.0, clock=lambda: next(ticks, 10_000.0))
    assert stats.stopped == "max_hours"
    assert stats.processed == 1


@pytest.mark.unit
def test_status_command(tmp_path: Any, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "collector.sqlite"
    Frontier(db).seed(
        [{"name": "a", "point_id": "1", "repo_url": None, "stars": 1, "sourcerank": 0}]
    )
    assert cli_main(["--db", str(db), "status"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == 1 and out["never_checked"] == 1
