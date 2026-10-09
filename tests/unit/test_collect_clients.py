"""GitHub and PyPI clients against mocked HTTP responses."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from sarr.collect.github_client import CommitSignals, GitHubClient, GitHubError, parse_github_repo
from sarr.collect.http import PoliteClient
from sarr.collect.pypi_client import PyPIClient, release_cadence_days


def _polite(handler: Any) -> PoliteClient:
    return PoliteClient(
        user_agent="sarr-test", transport=httpx.MockTransport(handler), sleep=lambda s: None
    )


REPO_BODY = {
    "full_name": "psf/requests",
    "html_url": "https://github.com/psf/requests",
    "stargazers_count": 52000,
    "forks_count": 9400,
    "open_issues_count": 200,
    "pushed_at": "2026-10-01T00:00:00Z",
    "archived": False,
}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/psf/requests", ("psf", "requests")),
        ("https://github.com/psf/requests.git", ("psf", "requests")),
        ("http://www.github.com/psf/requests/tree/main", ("psf", "requests")),
        ("git@github.com:psf/requests.git", ("psf", "requests")),
        ("https://gitlab.com/a/b", None),
        ("https://github.com/psf", None),
        (None, None),
    ],
)
def test_parse_github_repo(url: str | None, expected: tuple[str, str] | None) -> None:
    assert parse_github_repo(url) == expected


@pytest.mark.unit
def test_repo_signals() -> None:
    client = GitHubClient(_polite(lambda r: httpx.Response(200, json=REPO_BODY)))
    repo = client.repo("psf", "requests")
    assert repo.status == "ok" and not repo.moved
    assert (repo.stars, repo.forks, repo.open_issues_count) == (52000, 9400, 200)


@pytest.mark.unit
def test_deleted_repo_is_gone() -> None:
    client = GitHubClient(_polite(lambda r: httpx.Response(404)))
    assert client.repo("ghost", "repo").status == "gone"


@pytest.mark.unit
def test_renamed_repo_follows_301_and_reports_move() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/kennethreitz/requests":
            return httpx.Response(
                301, headers={"Location": "https://api.github.com/repositories/1362490"}
            )
        return httpx.Response(200, json=REPO_BODY)

    repo = GitHubClient(_polite(handler)).repo("kennethreitz", "requests")
    assert repo.moved and repo.html_url == "https://github.com/psf/requests"


@pytest.mark.unit
def test_unexpected_status_raises() -> None:
    client = GitHubClient(_polite(lambda r: httpx.Response(401)))
    with pytest.raises(GitHubError):
        client.repo("psf", "requests")


@pytest.mark.unit
def test_recent_commits_and_empty_repo() -> None:
    commits = [
        {"commit": {"committer": {"date": "2026-10-01T00:00:00Z"}}},
        {"commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}},
    ]
    client = GitHubClient(_polite(lambda r: httpx.Response(200, json=commits)))
    signals = client.recent_commits("psf/requests")
    assert signals.last_commit == "2026-10-01T00:00:00+00:00"
    assert signals.commits_within(datetime(2026, 10, 9, tzinfo=UTC)) == 1

    empty = GitHubClient(_polite(lambda r: httpx.Response(409))).recent_commits("a/b")
    assert empty == CommitSignals()


def _file(when: str, yanked: bool = False) -> dict[str, Any]:
    return {"upload_time_iso_8601": when, "yanked": yanked}


@pytest.mark.unit
def test_pypi_project_signals() -> None:
    body = {
        "info": {"version": "2.0", "requires_python": ">=3.9"},
        "releases": {
            "1.0": [_file("2026-01-01T00:00:00Z")],
            "1.1": [_file("2026-01-31T00:00:00Z"), _file("2026-02-01T00:00:00Z")],
            "1.2": [_file("2026-03-02T00:00:00Z", yanked=True)],
            "2.0": [_file("2026-04-01T00:00:00Z")],
            "empty": [],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "pypi.org":
            return httpx.Response(200, json=body)
        return httpx.Response(200, json={"data": {"last_month": 1234}})

    client = PyPIClient(_polite(handler), _polite(handler))
    project = client.project("demo")
    assert project.status == "ok"
    assert project.latest_release == "2026-04-01T00:00:00+00:00"
    # Yanked 1.2 ignored: gaps 30 and 60 days -> median 45.
    assert project.release_cadence_days == 45.0
    assert project.requires_python == ">=3.9"
    assert client.downloads_30d("demo") == 1234


@pytest.mark.unit
def test_pypi_missing_project_and_downloads() -> None:
    missing = _polite(lambda r: httpx.Response(404))
    client = PyPIClient(missing, missing)
    assert client.project("nope").status == "gone"
    assert client.downloads_30d("nope") is None


@pytest.mark.unit
def test_release_cadence_needs_two_releases() -> None:
    assert release_cadence_days([]) is None
    assert release_cadence_days([datetime(2026, 1, 1, tzinfo=UTC)]) is None


@pytest.mark.unit
def test_downloads_rate_limited_returns_none_instead_of_failing() -> None:
    stats = PoliteClient(
        user_agent="sarr-test",
        transport=httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After": "0"})),
        sleep=lambda s: None,
        max_retries=2,
    )
    client = PyPIClient(_polite(lambda r: httpx.Response(404)), stats)
    assert client.downloads_30d("busy") is None
