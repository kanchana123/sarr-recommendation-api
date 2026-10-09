"""End-to-end refresh against mocked GitHub/PyPI and an in-memory payload store."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from sarr.collect.cli import main as cli_main
from sarr.collect.frontier import Frontier, SqliteResponseCache
from sarr.collect.github_client import GitHubClient
from sarr.collect.http import PoliteClient
from sarr.collect.pypi_client import PyPIClient
from sarr.collect.refresh import backfill, run_refresh

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


class MemoryStore:
    def __init__(self, points: dict[str, dict[str, Any]] | None = None) -> None:
        self.points = points or {}
        self.writes: list[tuple[str, dict[str, Any]]] = []

    def set_payload(self, point_id: str, payload: dict[str, Any]) -> None:
        self.writes.append((point_id, payload))
        self.points.setdefault(point_id, {}).update(payload)

    def scroll_payloads(self, fields: list[str]) -> Iterator[tuple[str, dict[str, Any]]]:
        for point_id, payload in self.points.items():
            yield point_id, {k: payload.get(k) for k in fields}


class FakeWeb:
    """Routes GitHub, PyPI and pypistats URLs; ETags make reruns cheap."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.gone = {"ghost"}
        self.broken: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        host = request.url.host
        etag = f'"{path}"'
        if request.headers.get("If-None-Match") == etag:
            return httpx.Response(304)
        if host == "api.github.com":
            repo = path.split("/")[3]
            if repo in self.broken:
                return httpx.Response(503)
            if repo in self.gone:
                return httpx.Response(404)
            if path.endswith("/commits"):
                body: Any = [{"commit": {"committer": {"date": "2026-10-01T00:00:00Z"}}}]
            else:
                body = {
                    "full_name": f"o/{repo}",
                    "html_url": f"https://github.com/o/{repo}",
                    "stargazers_count": 1234,
                    "forks_count": 5,
                    "open_issues_count": 3,
                    "pushed_at": "2026-10-02T00:00:00Z",
                    "archived": False,
                }
            return httpx.Response(200, json=body, headers={"ETag": etag})
        if host == "pypi.org":
            body = {
                "info": {"version": "1.0", "requires_python": ">=3.10"},
                "releases": {
                    "0.9": [{"upload_time_iso_8601": "2026-08-01T00:00:00Z"}],
                    "1.0": [{"upload_time_iso_8601": "2026-09-01T00:00:00Z"}],
                },
            }
            return httpx.Response(200, json=body, headers={"ETag": etag})
        return httpx.Response(200, json={"data": {"last_month": 42}})

    def count(self, host: str) -> int:
        return sum(1 for r in self.requests if r.url.host == host)


def _clients(web: FakeWeb, frontier: Frontier) -> tuple[GitHubClient, PyPIClient]:
    cache = SqliteResponseCache(frontier)

    def polite() -> PoliteClient:
        return PoliteClient(
            user_agent="sarr-test",
            transport=httpx.MockTransport(web),
            cache=cache,
            sleep=lambda s: None,
            max_retries=1,
        )

    return GitHubClient(polite()), PyPIClient(polite(), polite())


def _store() -> MemoryStore:
    def point(name: str, stars: int, repo: str | None) -> dict[str, Any]:
        return {"name": name, "stars": stars, "sourcerank": 1, "repo_url": repo}

    return MemoryStore(
        {
            "p-requests": point("requests", 50_000, "https://github.com/o/requests"),
            "p-httpx": point("httpx", 12_000, "https://github.com/o/httpx"),
            "p-ghost": point("ghost", 900, "https://github.com/o/ghost"),
            "p-gitlab": point("gitlabpkg", 99_999, "https://gitlab.com/o/gitlabpkg"),
            "p-norepo": point("norepo", 5, None),
        }
    )


def _refresh(frontier: Frontier, web: FakeWeb, store: MemoryStore, **kwargs: Any) -> Any:
    github, pypi = _clients(web, frontier)
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
