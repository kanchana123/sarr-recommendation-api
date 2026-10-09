"""Turn fetched GitHub/PyPI signals into Qdrant payload fields and a 0..1 health score."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Any

from sarr.collect.github_client import CommitSignals, RepoSignals
from sarr.collect.pypi_client import PypiSignals

# Weights for health_score components; they sum to 1.
W_RECENCY = 0.35
W_RELEASES = 0.25
W_ISSUES = 0.15
W_ACTIVITY = 0.25
SCORE_HASH_STEP = 0.05


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _decay(when: datetime | None, now: datetime, scale_days: float) -> float:
    # Same exp-decay shape as ranking._recency_score.
    if when is None:
        return 0.0
    days = max((now - when).total_seconds() / 86400.0, 0.0)
    return math.exp(-days / scale_days)


def health_score(
    *,
    last_activity: datetime | None,
    latest_release: datetime | None,
    release_cadence_days: float | None,
    open_issues_count: int | None,
    stars: int | None,
    commits_90d: int,
    now: datetime,
) -> float:
    recency = _decay(last_activity, now, 180.0)
    cadence = math.exp(-release_cadence_days / 180.0) if release_cadence_days else 0.0
    releases = 0.5 * _decay(latest_release, now, 365.0) + 0.5 * cadence
    if open_issues_count is None:
        issues = 0.5
    else:
        # Open issues per star; 0.1 (e.g. 100 issues on 1k stars) scores 0.5.
        issues = 1.0 / (1.0 + 10.0 * open_issues_count / ((stars or 0) + 1))
    activity = 1.0 if commits_90d > 0 else 0.0
    score = W_RECENCY * recency + W_RELEASES * releases + W_ISSUES * issues + W_ACTIVITY * activity
    return round(min(max(score, 0.0), 1.0), 2)


def build_health_fields(
    repo: RepoSignals,
    commits: CommitSignals | None,
    pypi: PypiSignals | None,
    downloads_30d: int | None,
    *,
    original_repo_url: str | None,
    now: datetime,
) -> dict[str, Any]:
    """Payload fields to write. Keys with no fresh value are omitted, never nulled."""
    fields: dict[str, Any] = {"health_status": repo.status}

    if pypi is not None and pypi.status == "ok":
        fields["latest_release"] = pypi.latest_release
        fields["release_cadence_days"] = pypi.release_cadence_days
        fields["requires_python"] = pypi.requires_python
    if downloads_30d is not None:
        fields["downloads_30d"] = downloads_30d

    if repo.status == "gone":
        fields["health_score"] = 0.0
        return {k: v for k, v in fields.items() if v is not None}

    commits = commits or CommitSignals()
    commits_90d = commits.commits_within(now)
    last_commit = commits.last_commit or repo.pushed_at
    fields.update(
        stars=repo.stars,
        forks=repo.forks,
        open_issues_count=repo.open_issues_count,
        last_commit=last_commit,
        commits_90d=commits_90d,
        archived=repo.archived,
    )
    if repo.moved and repo.html_url and repo.html_url != original_repo_url:
        fields["repo_url"] = repo.html_url

    score = health_score(
        last_activity=_parse(last_commit),
        latest_release=_parse(fields.get("latest_release")),
        release_cadence_days=fields.get("release_cadence_days"),
        open_issues_count=repo.open_issues_count,
        stars=repo.stars,
        commits_90d=commits_90d,
        now=now,
    )
    # Archived repos are read-only by declaration; cap them regardless of history.
    fields["health_score"] = min(score, 0.2) if repo.archived else score
    return {k: v for k, v in fields.items() if v is not None}


def health_hash(fields: dict[str, Any]) -> str:
    """Stable digest of the health fields; equal digests mean the write can be skipped."""
    hashed = dict(fields)
    if "health_score" in hashed:
        # health_score decays daily even when no signal changes. Hash it in
        # SCORE_HASH_STEP buckets so the stored score lags by at most one step.
        hashed["health_score"] = round(hashed["health_score"] / SCORE_HASH_STEP)
    canonical = json.dumps(hashed, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()
