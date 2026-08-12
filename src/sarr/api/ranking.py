"""Score blending: semantic relevance + popularity + recency."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any


def _log_pop(value: int | float | None) -> float:
    if value is None or value <= 0:
        return 0.0
    return math.log1p(float(value))


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _recency_score(when: datetime | None, now: datetime | None = None) -> float:
    if when is None:
        return 0.0
    current = now or datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    days = max((current - when).total_seconds() / 86400.0, 0.0)
    # ~1.0 if today, ~0.5 around 180 days, approaches 0 for old activity
    return math.exp(-days / 180.0)


def blend_scores(
    relevance: float,
    payload: dict[str, Any],
    *,
    alpha: float = 0.75,
    beta: float = 0.15,
    delta: float = 0.10,
) -> float:
    stars = payload.get("stars") or 0
    downloads = payload.get("downloads_30d")
    dependents = payload.get("dependent_projects_count") or 0
    sourcerank = payload.get("sourcerank") or 0

    popularity = _log_pop(stars)
    if downloads is not None:
        popularity = 0.5 * popularity + 0.5 * _log_pop(downloads)
    elif dependents:
        popularity = 0.6 * popularity + 0.4 * _log_pop(dependents)
    if sourcerank:
        popularity = 0.85 * popularity + 0.15 * _log_pop(sourcerank)

    # Normalize popularity roughly into 0..1 for MVP (log1p(1e5) ≈ 11.5)
    popularity_norm = min(popularity / 11.5, 1.0)

    when = _parse_dt(payload.get("last_commit")) or _parse_dt(payload.get("latest_release"))
    recency = _recency_score(when)

    return alpha * relevance + beta * popularity_norm + delta * recency
