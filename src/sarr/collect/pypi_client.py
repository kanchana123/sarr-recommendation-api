"""PyPI JSON API and pypistats clients for release and download signals."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sarr.collect.http import PoliteClient, ResponseCache, RetriesExhaustedError
from sarr.common.config import Settings

PYPI_API = "https://pypi.org/pypi"
PYPISTATS_API = "https://pypistats.org/api/packages"
CADENCE_RELEASES = 10


class PyPIError(RuntimeError):
    pass


@dataclass
class PypiSignals:
    status: str  # "ok" | "gone"
    latest_version: str | None = None
    latest_release: str | None = None
    release_cadence_days: float | None = None
    requires_python: str | None = None


def _release_dates(releases: dict[str, list[dict[str, Any]]]) -> list[datetime]:
    # A release's date is its earliest non-yanked file upload.
    dates: list[datetime] = []
    for files in releases.values():
        uploads = [
            f["upload_time_iso_8601"]
            for f in files
            if f.get("upload_time_iso_8601") and not f.get("yanked")
        ]
        if uploads:
            dates.append(datetime.fromisoformat(min(uploads).replace("Z", "+00:00")))
    return sorted(dates)


def release_cadence_days(dates: list[datetime]) -> float | None:
    """Median days between the most recent releases; None with fewer than two."""
    recent = dates[-CADENCE_RELEASES:]
    if len(recent) < 2:
        return None
    gaps = [(b - a).total_seconds() / 86400.0 for a, b in zip(recent, recent[1:], strict=False)]
    return round(statistics.median(gaps), 1)


def _parse_project(body: dict[str, Any]) -> dict[str, Any]:
    info = body.get("info") or {}
    dates = _release_dates(body.get("releases") or {})
    return {
        "latest_version": info.get("version"),
        "latest_release": dates[-1].isoformat() if dates else None,
        "release_cadence_days": release_cadence_days(dates),
        "requires_python": info.get("requires_python") or None,
    }


def _parse_downloads(body: dict[str, Any]) -> int | None:
    value = (body.get("data") or {}).get("last_month")
    return int(value) if value is not None else None


class PyPIClient:
    def __init__(self, pypi: PoliteClient, stats: PoliteClient) -> None:
        self.pypi = pypi
        self.stats = stats

    @classmethod
    def from_settings(cls, settings: Settings, cache: ResponseCache | None = None) -> PyPIClient:
        return cls(
            pypi=PoliteClient(
                user_agent=settings.collector_user_agent, min_interval_s=0.1, cache=cache
            ),
            # pypistats is a small volunteer service allowing 60 requests/minute.
            stats=PoliteClient(
                user_agent=settings.collector_user_agent,
                min_interval_s=1.1,
                low_remaining=3,
                cache=cache,
            ),
        )

    def project(self, name: str) -> PypiSignals:
        result = self.pypi.get_json(f"{PYPI_API}/{name}/json", parse=_parse_project)
        if result.status == 404:
            return PypiSignals(status="gone")
        if result.status != 200:
            raise PyPIError(f"PyPI {name} returned HTTP {result.status}")
        return PypiSignals(status="ok", **result.data)

    def downloads_30d(self, name: str) -> int | None:
        """Downloads are optional; a pypistats outage must not fail the package."""
        try:
            result = self.stats.get_json(f"{PYPISTATS_API}/{name}/recent", parse=_parse_downloads)
        except RetriesExhaustedError:
            return None
        if result.status != 200:
            return None
        downloads: int | None = result.data
        return downloads

    @property
    def deadline(self) -> float | None:
        return self.pypi.deadline

    @deadline.setter
    def deadline(self, value: float | None) -> None:
        self.pypi.deadline = value
        self.stats.deadline = value
