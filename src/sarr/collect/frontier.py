"""Persistent crawl frontier (SQLite): which packages to refresh next, plus an ETag cache."""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Never-checked packages count as this stale, so they outrank most rechecks.
NEVER_CHECKED_DAYS = 365.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS packages (
    name TEXT PRIMARY KEY,
    point_id TEXT NOT NULL,
    repo_url TEXT,
    stars INTEGER NOT NULL DEFAULT 0,
    sourcerank INTEGER NOT NULL DEFAULT 0,
    health_checked_at TEXT,
    health_hash TEXT,
    health_status TEXT,
    failures INTEGER NOT NULL DEFAULT 0,
    dead INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS packages_due ON packages (dead, health_checked_at);
CREATE TABLE IF NOT EXISTS http_cache (
    key TEXT PRIMARY KEY,
    etag TEXT NOT NULL,
    data TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    stats TEXT NOT NULL
);
"""


def utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat()


@dataclass
class FrontierItem:
    name: str
    point_id: str
    repo_url: str | None
    stars: int
    sourcerank: int
    health_checked_at: str | None
    health_hash: str | None
    failures: int
    priority: float


class Frontier:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.create_function("log1p", 1, math.log1p, deterministic=True)
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def seed(self, rows: Iterable[dict[str, Any]]) -> int:
        """Insert packages; existing rows keep their health state. Returns new-row count."""
        before = self.count()
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO packages (name, point_id, repo_url, stars, sourcerank)
                VALUES (:name, :point_id, :repo_url, :stars, :sourcerank)
                ON CONFLICT(name) DO UPDATE SET
                    point_id = excluded.point_id,
                    repo_url = CASE WHEN packages.health_checked_at IS NULL
                                    THEN excluded.repo_url ELSE packages.repo_url END,
                    stars = CASE WHEN packages.health_checked_at IS NULL
                                 THEN excluded.stars ELSE packages.stars END,
                    sourcerank = excluded.sourcerank
                """,
                rows,
            )
        return self.count() - before

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM packages").fetchone()[0])

    def due(self, *, limit: int, stale_days: int, now: datetime) -> list[FrontierItem]:
        """Packages never checked or older than stale_days, stale + popular first."""
        rows = self.conn.execute(
            """
            SELECT *,
                (CASE WHEN health_checked_at IS NULL THEN :never
                      ELSE julianday(:now) - julianday(health_checked_at) END)
                * log1p(stars + sourcerank) AS priority
            FROM packages
            WHERE dead = 0 AND (health_checked_at IS NULL OR health_checked_at <= :cutoff)
            ORDER BY priority DESC, stars DESC, name
            LIMIT :limit
            """,
            {
                "never": NEVER_CHECKED_DAYS,
                "now": utc_iso(now),
                "cutoff": utc_iso(now - timedelta(days=stale_days)),
                "limit": limit,
            },
        ).fetchall()
        return [
            FrontierItem(
                name=r["name"],
                point_id=r["point_id"],
                repo_url=r["repo_url"],
                stars=r["stars"],
                sourcerank=r["sourcerank"],
                health_checked_at=r["health_checked_at"],
                health_hash=r["health_hash"],
                failures=r["failures"],
                priority=float(r["priority"]),
            )
            for r in rows
        ]

    def mark_checked(
        self,
        name: str,
        *,
        checked_at: datetime,
        health_hash: str,
        health_status: str,
        stars: int | None = None,
        repo_url: str | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE packages SET
                    health_checked_at = :checked_at,
                    health_hash = :health_hash,
                    health_status = :health_status,
                    stars = COALESCE(:stars, stars),
                    repo_url = COALESCE(:repo_url, repo_url),
                    failures = 0,
                    last_error = NULL
                WHERE name = :name
                """,
                {
                    "name": name,
                    "checked_at": utc_iso(checked_at),
                    "health_hash": health_hash,
                    "health_status": health_status,
                    "stars": stars,
                    "repo_url": repo_url,
                },
            )

    def mark_failure(self, name: str, error: str, *, max_failures: int) -> bool:
        """Record a failed fetch. Returns True when the package is now dead-lettered."""
        with self.conn:
            self.conn.execute(
                """
                UPDATE packages SET
                    failures = failures + 1,
                    last_error = :error,
                    dead = CASE WHEN failures + 1 >= :max_failures THEN 1 ELSE 0 END
                WHERE name = :name
                """,
                {"name": name, "error": error[:500], "max_failures": max_failures},
            )
        row = self.conn.execute("SELECT dead FROM packages WHERE name = ?", (name,)).fetchone()
        return bool(row and row["dead"])

    def record_run(
        self, started_at: datetime, finished_at: datetime, stats: dict[str, Any]
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO runs (started_at, finished_at, stats) VALUES (?, ?, ?)",
                (utc_iso(started_at), utc_iso(finished_at), json.dumps(stats)),
            )

    def stats(self, *, stale_days: int, now: datetime) -> dict[str, Any]:
        cutoff = utc_iso(now - timedelta(days=stale_days))
        row = self.conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(health_checked_at IS NULL AND dead = 0) AS never_checked,
                SUM(health_checked_at IS NOT NULL) AS checked,
                SUM(dead = 0 AND (health_checked_at IS NULL OR health_checked_at <= ?)) AS due,
                SUM(failures > 0 AND dead = 0) AS failing,
                SUM(dead = 1) AS dead_lettered,
                SUM(health_status = 'gone') AS gone,
                MIN(health_checked_at) AS oldest_check,
                MAX(health_checked_at) AS newest_check
            FROM packages
            """,
            (cutoff,),
        ).fetchone()
        result = {key: row[key] or 0 for key in row.keys()}
        result["oldest_check"] = row["oldest_check"]
        result["newest_check"] = row["newest_check"]
        last = self.conn.execute(
            "SELECT finished_at, stats FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        result["last_run"] = (
            {"finished_at": last["finished_at"], **json.loads(last["stats"])} if last else None
        )
        return result


class SqliteResponseCache:
    """ETag cache for PoliteClient, stored next to the frontier."""

    def __init__(self, frontier: Frontier) -> None:
        self.conn = frontier.conn

    def get(self, key: str) -> tuple[str, Any] | None:
        row = self.conn.execute(
            "SELECT etag, data FROM http_cache WHERE key = ?", (key,)
        ).fetchone()
        return (row["etag"], json.loads(row["data"])) if row else None

    def put(self, key: str, etag: str, data: Any) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO http_cache (key, etag, data, fetched_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    etag = excluded.etag, data = excluded.data, fetched_at = excluded.fetched_at
                """,
                (key, etag, json.dumps(data), utc_iso(datetime.now(UTC))),
            )
