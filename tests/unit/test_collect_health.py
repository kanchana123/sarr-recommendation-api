"""Health score and payload field construction."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sarr.collect.github_client import CommitSignals, RepoSignals
from sarr.collect.health import build_health_fields, health_hash, health_score
from sarr.collect.pypi_client import PypiSignals

NOW = datetime(2026, 10, 9, tzinfo=UTC)


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def _repo(**kwargs: object) -> RepoSignals:
    base: dict[str, object] = {
        "status": "ok",
        "full_name": "o/r",
        "html_url": "https://github.com/o/r",
        "stars": 1000,
        "forks": 10,
        "open_issues_count": 20,
        "pushed_at": _iso(2),
    }
    base.update(kwargs)
    return RepoSignals(**base)  # type: ignore[arg-type]


@pytest.mark.unit
def test_active_package_scores_higher_than_abandoned() -> None:
    active = health_score(
        last_activity=NOW - timedelta(days=3),
        latest_release=NOW - timedelta(days=20),
        release_cadence_days=30,
        open_issues_count=20,
        stars=1000,
        commits_90d=5,
        now=NOW,
    )
    abandoned = health_score(
        last_activity=NOW - timedelta(days=1500),
        latest_release=NOW - timedelta(days=1100),
        release_cadence_days=None,
        open_issues_count=400,
        stars=100,
        commits_90d=0,
        now=NOW,
    )
    assert 0.0 <= abandoned < 0.2 < 0.8 < active <= 1.0


@pytest.mark.unit
def test_build_fields_for_healthy_repo() -> None:
    commits = CommitSignals(last_commit=_iso(1), commit_dates=[_iso(1), _iso(40), _iso(200)])
    pypi = PypiSignals(
        status="ok", latest_release=_iso(10), release_cadence_days=30.0, requires_python=">=3.9"
    )
    fields = build_health_fields(
        _repo(), commits, pypi, 5000, original_repo_url="https://github.com/o/r", now=NOW
    )
    assert fields["health_status"] == "ok"
    assert fields["commits_90d"] == 2
    assert fields["last_commit"] == _iso(1)
    assert fields["downloads_30d"] == 5000
    assert fields["requires_python"] == ">=3.9"
    assert "repo_url" not in fields
    assert 0.0 < fields["health_score"] <= 1.0


@pytest.mark.unit
def test_missing_values_are_omitted_not_nulled() -> None:
    fields = build_health_fields(
        _repo(open_issues_count=None),
        None,
        PypiSignals(status="gone"),
        None,
        original_repo_url=None,
        now=NOW,
    )
    assert None not in fields.values()
    assert "downloads_30d" not in fields and "latest_release" not in fields
    # No commit sample: last push stands in for last commit.
    assert fields["last_commit"] == _iso(2)


@pytest.mark.unit
def test_gone_repo_scores_zero() -> None:
    fields = build_health_fields(
        RepoSignals(status="gone"), None, None, None, original_repo_url=None, now=NOW
    )
    assert fields == {"health_status": "gone", "health_score": 0.0}


@pytest.mark.unit
def test_moved_repo_updates_repo_url() -> None:
    fields = build_health_fields(
        _repo(moved=True, html_url="https://github.com/new/r"),
        None,
        None,
        None,
        original_repo_url="https://github.com/old/r",
        now=NOW,
    )
    assert fields["repo_url"] == "https://github.com/new/r"


@pytest.mark.unit
def test_archived_repo_is_capped() -> None:
    commits = CommitSignals(last_commit=_iso(1), commit_dates=[_iso(1)])
    fields = build_health_fields(
        _repo(archived=True), commits, None, None, original_repo_url=None, now=NOW
    )
    assert fields["health_score"] <= 0.2


@pytest.mark.unit
def test_health_hash_ignores_key_order() -> None:
    assert health_hash({"a": 1, "b": 2}) == health_hash({"b": 2, "a": 1})
    assert health_hash({"a": 1}) != health_hash({"a": 2})


@pytest.mark.unit
def test_health_hash_tolerates_small_score_drift() -> None:
    assert health_hash({"health_score": 0.81}) == health_hash({"health_score": 0.79})
    assert health_hash({"health_score": 0.81}) != health_hash({"health_score": 0.70})
