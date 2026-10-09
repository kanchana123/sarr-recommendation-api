"""AWS Lambda entry point for the scheduled refresh (infra/terraform/collector).

Each run: take an S3 lock, download the SQLite frontier, backfill if it doesn't
exist yet, refresh until the time budget runs out, upload the frontier, release
the lock. Secrets come from SSM Parameter Store, never from env values.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, TypeVar

from sarr.collect.frontier import Frontier, SqliteResponseCache
from sarr.collect.github_client import GitHubClient
from sarr.collect.pypi_client import PyPIClient
from sarr.collect.refresh import PayloadStore, backfill, run_refresh
from sarr.common.config import Settings

logger = logging.getLogger("sarr.collect")

STATE_PATH = "/tmp/collector.sqlite"
# Leave time to close the frontier, upload it and release the lock.
SAFETY_MARGIN_S = 90.0
MIN_REFRESH_BUDGET_S = 60.0
LOCK_TTL_S = 1800.0  # Lambda runs at most 15 minutes; older locks are abandoned.
METRIC_NAMESPACE = "SARR/Collector"
SECRET_PARAMS = {"GITHUB_TOKEN": "GITHUB_TOKEN_PARAM", "QDRANT_API_KEY": "QDRANT_API_KEY_PARAM"}
# Must match the initial value in infra/terraform/collector/ssm.tf.
SECRET_PLACEHOLDER = "SET_ME"

T = TypeVar("T")
ClientFactory = Callable[[Settings, SqliteResponseCache], T]


class LockHeldError(RuntimeError):
    pass


class S3State:
    """Frontier file and run lock in S3."""

    def __init__(self, s3: Any, bucket: str, key: str) -> None:
        self.s3 = s3
        self.bucket = bucket
        self.key = key
        self.lock_key = f"{key}.lock"

    def acquire_lock(self, run_id: str, now: datetime) -> None:
        if self._try_put_lock(run_id):
            return
        head = self.s3.head_object(Bucket=self.bucket, Key=self.lock_key)
        age_s = (now - head["LastModified"]).total_seconds()
        if age_s < LOCK_TTL_S:
            raise LockHeldError(f"another run holds {self.lock_key} ({age_s:.0f}s old)")
        logger.warning("removing stale lock age_s=%.0f", age_s)
        self.s3.delete_object(Bucket=self.bucket, Key=self.lock_key)
        if not self._try_put_lock(run_id):
            raise LockHeldError(f"lost the race for {self.lock_key}")

    def release_lock(self) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=self.lock_key)

    def download(self, path: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.s3.download_file(self.bucket, self.key, path)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                return False
            raise
        return True

    def upload(self, path: str) -> None:
        self.s3.upload_file(path, self.bucket, self.key)

    def _try_put_lock(self, run_id: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            # S3 conditional write: fails if the lock object already exists.
            self.s3.put_object(
                Bucket=self.bucket, Key=self.lock_key, Body=run_id.encode(), IfNoneMatch="*"
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("PreconditionFailed", "ConditionalRequestConflict"):
                return False
            raise
        return True


def load_secrets(ssm: Any) -> None:
    """Resolve SSM SecureString parameters into env vars before Settings is built."""
    for env_name, param_env in SECRET_PARAMS.items():
        name = os.environ.get(param_env)
        if name and not os.environ.get(env_name):
            response = ssm.get_parameter(Name=name, WithDecryption=True)
            value = response["Parameter"]["Value"]
            if value == SECRET_PLACEHOLDER:
                raise RuntimeError(f"SSM parameter {name} still holds the Terraform placeholder")
            os.environ[env_name] = value


def emit_metrics(stats: dict[str, Any], now: datetime) -> None:
    """CloudWatch Embedded Metric Format: one log line becomes custom metrics."""
    names = ["selected", "processed", "written", "unchanged", "gone", "moved", "failed"]
    names.append("dead_lettered")
    metrics = {name: stats.get(name, 0) for name in names}
    print(
        json.dumps(
            {
                "_aws": {
                    "Timestamp": int(now.timestamp() * 1000),
                    "CloudWatchMetrics": [
                        {
                            "Namespace": METRIC_NAMESPACE,
                            "Dimensions": [[]],
                            "Metrics": [{"Name": n, "Unit": "Count"} for n in metrics],
                        }
                    ],
                },
                **metrics,
            }
        )
    )


def run(
    event: dict[str, Any],
    *,
    remaining_s: float,
    s3: Any,
    ssm: Any,
    make_store: Callable[[Settings], PayloadStore] | None = None,
    make_github: ClientFactory[GitHubClient] = GitHubClient.from_settings,
    make_pypi: ClientFactory[PyPIClient] = PyPIClient.from_settings,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    started = clock()
    load_secrets(ssm)
    settings = Settings()
    if make_store is None:
        from sarr.collect.store import QdrantPayloadStore

        make_store = QdrantPayloadStore

    command = event.get("command", "refresh")
    state = S3State(
        s3,
        os.environ["COLLECTOR_STATE_BUCKET"],
        os.environ.get("COLLECTOR_STATE_KEY", "collector/collector.sqlite"),
    )
    state.acquire_lock(uuid.uuid4().hex, now())
    result: dict[str, Any] = {"command": command}
    try:
        if os.path.exists(STATE_PATH):
            os.remove(STATE_PATH)  # warm container: never reuse a stale local copy
        existed = state.download(STATE_PATH)
        frontier = Frontier(STATE_PATH)
        try:
            store = make_store(settings)
            if command == "backfill" or not existed:
                top_n = int(event.get("top_n") or os.environ.get("BACKFILL_TOP_N", "10000"))
                result["backfill"] = backfill(frontier, store, top_n=top_n)

            budget_s = remaining_s - (clock() - started) - SAFETY_MARGIN_S
            if command == "refresh" and budget_s >= MIN_REFRESH_BUDGET_S:
                cache = SqliteResponseCache(frontier)
                stats = run_refresh(
                    frontier,
                    make_github(settings, cache),
                    make_pypi(settings, cache),
                    store,
                    limit=int(event.get("limit") or os.environ.get("REFRESH_LIMIT", "5000")),
                    stale_days=settings.collector_stale_days,
                    max_failures=settings.collector_max_failures,
                    max_hours=budget_s / 3600.0,
                    with_downloads=os.environ.get("WITH_DOWNLOADS", "true").lower() == "true",
                )
                result["refresh"] = asdict(stats)
                emit_metrics(result["refresh"], now())
            result["frontier"] = frontier.stats(stale_days=settings.collector_stale_days, now=now())
        finally:
            frontier.close()
            # Upload even after a failure: per-package progress is already committed.
            state.upload(STATE_PATH)
    finally:
        state.release_lock()

    logger.info("collector run done %s", json.dumps(result, default=str))
    return result


def handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    import boto3

    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("sarr").setLevel(logging.INFO)
    return run(
        event or {},
        remaining_s=context.get_remaining_time_in_millis() / 1000.0,
        s3=boto3.client("s3"),
        ssm=boto3.client("ssm"),
    )
