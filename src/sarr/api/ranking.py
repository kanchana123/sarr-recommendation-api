"""Score blending: semantic relevance + popularity + recency."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any


def _log_pop(value: int | float | None) -> float:
    if value is None or value <= 0:
        return 0.0
    return math.log1p(float(value))


def _recency_score(last_commit: datetime | None, now: datetime | None = None) -> float:
    if last_commit is None:
        return 0.0
    current = now or datetime.now(UTC)
    if last_commit.tzinfo is None:
        last_commit = last_commit.replace(tzinfo=UTC)
    days = max((current - last_commit).total_seconds() / 86400.0, 0.0)
    # ~1.0 if committed today, ~0.5 around 180 days, approaches 0 for old repos
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
    last_commit = payload.get("last_commit")
    if isinstance(last_commit, str):
        try:
            last_commit = datetime.fromisoformat(last_commit.replace("Z", "+00:00"))
        except ValueError:
            last_commit = None

    popularity = _log_pop(stars)
    if downloads is not None:
        popularity = 0.5 * popularity + 0.5 * _log_pop(downloads)

    # Normalize popularity roughly into 0..1 for MVP (log1p(1e5) ≈ 11.5)
    popularity_norm = min(popularity / 11.5, 1.0)
    recency = _recency_score(last_commit if isinstance(last_commit, datetime) else None)

    return alpha * relevance + beta * popularity_norm + delta * recency
