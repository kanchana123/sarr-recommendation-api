"""Backfill the frontier from Qdrant, then refresh due packages: fetch, compare, update."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sarr.collect.frontier import Frontier, FrontierItem, utc_iso
from sarr.collect.github_client import GitHubClient, parse_github_repo
from sarr.collect.health import build_health_fields, health_hash
from sarr.collect.http import BudgetExhaustedError
from sarr.collect.pypi_client import PyPIClient

logger = logging.getLogger("sarr.collect")

SEED_FIELDS = ["name", "repo_url", "stars", "sourcerank"]


class PayloadStore(Protocol):
    def set_payload(self, point_id: str, payload: dict[str, Any]) -> None: ...

    def scroll_payloads(self, fields: list[str]) -> Any: ...


@dataclass
class RefreshStats:
    selected: int = 0
    processed: int = 0
    written: int = 0
    unchanged: int = 0
    gone: int = 0
    moved: int = 0
    failed: int = 0
    dead_lettered: int = 0
    stopped: str = "completed"


def backfill(frontier: Frontier, store: PayloadStore, *, top_n: int) -> dict[str, int]:
    """Seed the frontier with the top_n most-starred packages that link to GitHub."""
    candidates: list[dict[str, Any]] = []
    scanned = 0
    for point_id, payload in store.scroll_payloads(SEED_FIELDS):
        scanned += 1
        if not payload.get("name") or parse_github_repo(payload.get("repo_url")) is None:
            continue
        candidates.append(
            {
                "name": payload["name"],
                "point_id": point_id,
                "repo_url": payload["repo_url"],
                "stars": int(payload.get("stars") or 0),
                "sourcerank": int(payload.get("sourcerank") or 0),
            }
        )
    candidates.sort(key=lambda r: (r["stars"], r["sourcerank"], r["name"]), reverse=True)
    selected = candidates[:top_n]
    added = frontier.seed(selected)
    return {
        "scanned": scanned,
        "github_linked": len(candidates),
        "selected": len(selected),
        "added": added,
        "frontier_total": frontier.count(),
    }


def run_refresh(
    frontier: Frontier,
    github: GitHubClient,
    pypi: PyPIClient,
    store: PayloadStore,
    *,
    limit: int,
    stale_days: int,
    max_failures: int,
    max_hours: float | None = None,
    with_downloads: bool = True,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    clock: Callable[[], float] = time.time,
) -> RefreshStats:
    started_at = now()
    stats = RefreshStats()
    deadline = clock() + max_hours * 3600.0 if max_hours else None
    github.http.deadline = deadline
    pypi.deadline = deadline

    items = frontier.due(limit=limit, stale_days=stale_days, now=started_at)
    stats.selected = len(items)
    for item in items:
        if deadline is not None and clock() >= deadline:
            stats.stopped = "max_hours"
            break
        try:
            _refresh_one(item, frontier, github, pypi, store, stats, with_downloads, now())
        except BudgetExhaustedError:
            stats.stopped = "max_hours"
            break
        except Exception as exc:  # noqa: BLE001 — one bad package must not stop the run
            logger.warning("refresh failed package=%s error=%s", item.name, exc)
            stats.failed += 1
            if frontier.mark_failure(item.name, str(exc), max_failures=max_failures):
                stats.dead_lettered += 1
                store.set_payload(
                    item.point_id,
                    {"health_status": "error", "health_checked_at": utc_iso(now())},
                )
        stats.processed += 1

    frontier.record_run(started_at, now(), asdict(stats))
    return stats


def _refresh_one(
    item: FrontierItem,
    frontier: Frontier,
    github: GitHubClient,
    pypi: PyPIClient,
    store: PayloadStore,
    stats: RefreshStats,
    with_downloads: bool,
    checked_at: datetime,
) -> None:
    parsed = parse_github_repo(item.repo_url)
    if parsed is None:
        raise ValueError(f"not a GitHub repo url: {item.repo_url!r}")

    repo = github.repo(*parsed)
    commits = (
        github.recent_commits(repo.full_name) if repo.status == "ok" and repo.full_name else None
    )
    project = pypi.project(item.name)
    downloads = pypi.downloads_30d(item.name) if with_downloads else None

    fields = build_health_fields(
        repo, commits, project, downloads, original_repo_url=item.repo_url, now=checked_at
    )
    digest = health_hash(fields)
    if digest == item.health_hash:
        # Dedup: nothing changed, so only the freshness timestamp is written.
        store.set_payload(item.point_id, {"health_checked_at": utc_iso(checked_at)})
        stats.unchanged += 1
    else:
        store.set_payload(item.point_id, {**fields, "health_checked_at": utc_iso(checked_at)})
        stats.written += 1

    if repo.status == "gone":
        stats.gone += 1
    if "repo_url" in fields:
        stats.moved += 1
        logger.info("repo moved package=%s %s -> %s", item.name, item.repo_url, fields["repo_url"])
    frontier.mark_checked(
        item.name,
        checked_at=checked_at,
        health_hash=digest,
        health_status=fields["health_status"],
        stars=fields.get("stars"),
        repo_url=fields.get("repo_url"),
    )
