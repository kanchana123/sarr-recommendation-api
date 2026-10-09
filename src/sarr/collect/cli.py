"""`sarr-collect` CLI: backfill, refresh, status."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from datetime import UTC, datetime

from sarr.collect.frontier import Frontier, SqliteResponseCache
from sarr.collect.github_client import GitHubClient
from sarr.collect.pypi_client import PyPIClient
from sarr.collect.refresh import backfill, run_refresh
from sarr.collect.store import QdrantPayloadStore
from sarr.common.config import get_settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sarr-collect", description="Refresh package health signals into Qdrant."
    )
    parser.add_argument("--db", default=None, help="Frontier SQLite path (COLLECTOR_DB_PATH).")
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("backfill", help="Seed the frontier from Qdrant payloads.")
    seed.add_argument("--top-n", type=int, default=10_000)

    refresh = sub.add_parser("refresh", help="Refresh due packages (stale + popular first).")
    refresh.add_argument("--limit", type=int, default=1000)
    refresh.add_argument("--max-hours", type=float, default=None)
    refresh.add_argument(
        "--stale-days", type=int, default=None, help="Due when last check is older than this."
    )
    refresh.add_argument(
        "--no-downloads", action="store_true", help="Skip pypistats (slowest source)."
    )

    sub.add_parser("status", help="Frontier stats.")
    return parser


def _configure_logging() -> None:
    log = logging.getLogger("sarr")
    log.setLevel(logging.INFO)
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
        log.addHandler(handler)
    log.propagate = False


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _configure_logging()
    settings = get_settings()
    frontier = Frontier(args.db or settings.collector_db_path)
    stale_days = getattr(args, "stale_days", None)
    if stale_days is None:
        stale_days = settings.collector_stale_days

    try:
        if args.command == "status":
            result = frontier.stats(stale_days=stale_days, now=datetime.now(UTC))
        elif args.command == "backfill":
            result = backfill(frontier, QdrantPayloadStore(settings), top_n=args.top_n)
        else:
            if not settings.github_token:
                print(
                    "warning: GITHUB_TOKEN not set; GitHub allows 60 requests/hour",
                    file=sys.stderr,
                )
            cache = SqliteResponseCache(frontier)
            stats = run_refresh(
                frontier,
                GitHubClient.from_settings(settings, cache),
                PyPIClient.from_settings(settings, cache),
                QdrantPayloadStore(settings),
                limit=args.limit,
                stale_days=stale_days,
                max_failures=settings.collector_max_failures,
                max_hours=args.max_hours,
                with_downloads=not args.no_downloads,
            )
            result = asdict(stats)
    finally:
        frontier.close()

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
