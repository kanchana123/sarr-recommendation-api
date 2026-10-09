"""Shared fakes for collector tests: a fake GitHub/PyPI web and an in-memory payload store."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

from sarr.collect.github_client import GitHubClient
from sarr.collect.http import PoliteClient, ResponseCache
from sarr.collect.pypi_client import PyPIClient


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


def make_clients(web: FakeWeb, cache: ResponseCache) -> tuple[GitHubClient, PyPIClient]:
    def polite() -> PoliteClient:
        return PoliteClient(
            user_agent="sarr-test",
            transport=httpx.MockTransport(web),
            cache=cache,
            sleep=lambda s: None,
            max_retries=1,
        )

    return GitHubClient(polite()), PyPIClient(polite(), polite())


def make_store() -> MemoryStore:
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
