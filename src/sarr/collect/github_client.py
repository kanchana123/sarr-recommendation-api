"""GitHub REST client for repository health signals."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sarr.collect.http import FetchResult, PoliteClient, ResponseCache
from sarr.common.config import Settings

GITHUB_API = "https://api.github.com"
COMMIT_SAMPLE = 5
ACTIVITY_WINDOW_DAYS = 90

_REPO_RE = re.compile(r"github\.com[/:]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.IGNORECASE)


class GitHubError(RuntimeError):
    pass


def parse_github_repo(url: str | None) -> tuple[str, str] | None:
    if not url:
        return None
    match = _REPO_RE.search(url)
    if not match:
        return None
    owner, repo = match.group(1), match.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not repo or repo in {".", ".."}:
        return None
    return owner, repo


@dataclass
class RepoSignals:
    status: str  # "ok" | "gone"
    full_name: str | None = None
    html_url: str | None = None
    stars: int | None = None
    forks: int | None = None
    open_issues_count: int | None = None
    pushed_at: str | None = None
    archived: bool = False
    moved: bool = False


@dataclass
class CommitSignals:
    last_commit: str | None = None
    commit_dates: list[str] = field(default_factory=list)

    def commits_within(self, now: datetime, days: int = ACTIVITY_WINDOW_DAYS) -> int:
        cutoff = now - timedelta(days=days)
        return sum(1 for d in self.commit_dates if datetime.fromisoformat(d) >= cutoff)


def _parse_repo(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "full_name": body.get("full_name"),
        "html_url": body.get("html_url"),
        "stars": body.get("stargazers_count"),
        "forks": body.get("forks_count"),
        "open_issues_count": body.get("open_issues_count"),
        "pushed_at": body.get("pushed_at"),
        "archived": bool(body.get("archived")),
    }


def _parse_commits(body: list[dict[str, Any]]) -> list[str]:
    dates: list[str] = []
    for item in body:
        commit = item.get("commit") or {}
        when = (commit.get("committer") or {}).get("date") or (commit.get("author") or {}).get(
            "date"
        )
        if when:
            dates.append(when.replace("Z", "+00:00"))
    return dates


class GitHubClient:
    def __init__(self, http: PoliteClient) -> None:
        self.http = http

    @classmethod
    def from_settings(cls, settings: Settings, cache: ResponseCache | None = None) -> GitHubClient:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"
        return cls(
            PoliteClient(
                user_agent=settings.collector_user_agent,
                headers=headers,
                min_interval_s=0.1,
                cache=cache,
            )
        )

    def repo(self, owner: str, name: str) -> RepoSignals:
        result = self.http.get_json(f"{GITHUB_API}/repos/{owner}/{name}", parse=_parse_repo)
        # 451 = blocked for legal reasons (e.g. DMCA); treat like a deleted repo.
        if result.status in (404, 410, 451):
            return RepoSignals(status="gone")
        self._raise_for(result, f"repo {owner}/{name}")
        data = result.data
        full_name = data.get("full_name")
        moved = result.redirected or (
            full_name is not None and full_name.lower() != f"{owner}/{name}".lower()
        )
        return RepoSignals(status="ok", moved=moved, **data)

    def recent_commits(self, full_name: str) -> CommitSignals:
        # No `since` param: a stable URL keeps the ETag valid across runs.
        result = self.http.get_json(
            f"{GITHUB_API}/repos/{full_name}/commits",
            params={"per_page": COMMIT_SAMPLE},
            parse=_parse_commits,
        )
        if result.status in (404, 409):  # 409 = empty repository
            return CommitSignals()
        self._raise_for(result, f"commits {full_name}")
        dates: list[str] = result.data
        return CommitSignals(last_commit=dates[0] if dates else None, commit_dates=dates)

    @staticmethod
    def _raise_for(result: FetchResult, what: str) -> None:
        if result.status != 200:
            raise GitHubError(f"GitHub {what} returned HTTP {result.status}")
