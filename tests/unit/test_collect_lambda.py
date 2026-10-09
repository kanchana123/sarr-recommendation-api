"""Collector Lambda: S3 state + lock, SSM secrets, backfill-then-refresh, metrics."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError
from collect_fakes import FakeWeb, MemoryStore, make_clients
from collect_fakes import make_store as _store

from sarr.collect import lambda_handler
from sarr.collect.lambda_handler import LockHeldError, S3State, run

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, datetime]] = {}
        self.uploads = 0

    def put_object(self, Bucket: str, Key: str, Body: bytes, IfNoneMatch: str) -> None:  # noqa: N803
        if Key in self.objects:
            raise _client_error("PreconditionFailed")
        self.objects[Key] = (Body, NOW)

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        return {"LastModified": self.objects[Key][1]}

    def delete_object(self, Bucket: str, Key: str) -> None:  # noqa: N803
        self.objects.pop(Key, None)

    def download_file(self, bucket: str, key: str, path: str) -> None:
        if key not in self.objects:
            raise _client_error("404")
        Path(path).write_bytes(self.objects[key][0])

    def upload_file(self, path: str, bucket: str, key: str) -> None:
        self.uploads += 1
        self.objects[key] = (Path(path).read_bytes(), NOW)


class FakeSSM:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_parameter(self, Name: str, WithDecryption: bool) -> dict[str, Any]:  # noqa: N803
        self.calls.append(Name)
        return {"Parameter": {"Value": f"secret-from-{Name}"}}


STATE_KEY = "collector/collector.sqlite"


@pytest.fixture(autouse=True)
def lambda_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(lambda_handler, "STATE_PATH", str(tmp_path / "collector.sqlite"))
    monkeypatch.setenv("COLLECTOR_STATE_BUCKET", "bucket")
    monkeypatch.setenv("COLLECTOR_STATE_KEY", STATE_KEY)
    monkeypatch.setenv("GITHUB_TOKEN_PARAM", "/sarr/collector/github-token")
    monkeypatch.setenv("QDRANT_API_KEY_PARAM", "/sarr/collector/qdrant-api-key")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("QDRANT_API_KEY", raising=False)


def _run(s3: FakeS3, store: MemoryStore, web: FakeWeb, **kwargs: Any) -> dict[str, Any]:
    def github(settings: Any, cache: Any) -> Any:
        return make_clients(web, cache)[0]

    def pypi(settings: Any, cache: Any) -> Any:
        return make_clients(web, cache)[1]

    options: dict[str, Any] = {
        "remaining_s": 900.0,
        "s3": s3,
        "ssm": FakeSSM(),
        "make_store": lambda settings: store,
        "make_github": github,
        "make_pypi": pypi,
        "now": lambda: NOW,
    }
    options.update(kwargs)
    event = options.pop("event", {})
    return run(event, **options)


@pytest.mark.unit
def test_first_run_backfills_refreshes_and_uploads_state(
    capsys: pytest.CaptureFixture[str],
) -> None:
    s3, store, web = FakeS3(), _store(), FakeWeb()
    result = _run(s3, store, web)

    assert result["backfill"]["selected"] == 3
    assert result["refresh"]["written"] == 3
    assert result["frontier"]["checked"] == 3
    assert STATE_KEY in s3.objects
    assert f"{STATE_KEY}.lock" not in s3.objects
    assert store.points["p-requests"]["health_status"] == "ok"

    emf = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert emf["_aws"]["CloudWatchMetrics"][0]["Namespace"] == "SARR/Collector"
    assert emf["written"] == 3 and emf["failed"] == 0


@pytest.mark.unit
def test_second_run_reuses_uploaded_frontier() -> None:
    s3, store, web = FakeS3(), _store(), FakeWeb()
    _run(s3, store, web)
    result = _run(s3, store, web)
    assert "backfill" not in result
    assert result["refresh"]["selected"] == 0


@pytest.mark.unit
def test_backfill_command_does_not_refresh() -> None:
    s3, store, web = FakeS3(), _store(), FakeWeb()
    result = _run(s3, store, web, event={"command": "backfill", "top_n": 1})
    assert result["backfill"]["selected"] == 1
    assert "refresh" not in result
    assert web.requests == []


@pytest.mark.unit
def test_short_budget_skips_refresh_but_saves_state() -> None:
    s3, store, web = FakeS3(), _store(), FakeWeb()
    result = _run(s3, store, web, remaining_s=100.0)
    assert "refresh" not in result
    assert result["backfill"]["selected"] == 3
    assert s3.uploads == 1


@pytest.mark.unit
def test_secrets_come_from_ssm(monkeypatch: pytest.MonkeyPatch) -> None:
    ssm = FakeSSM()
    _run(FakeS3(), _store(), FakeWeb(), ssm=ssm)
    assert sorted(ssm.calls) == ["/sarr/collector/github-token", "/sarr/collector/qdrant-api-key"]
    import os

    assert os.environ["GITHUB_TOKEN"] == "secret-from-/sarr/collector/github-token"


@pytest.mark.unit
def test_unset_secret_placeholder_fails_before_any_work() -> None:
    class PlaceholderSSM(FakeSSM):
        def get_parameter(self, Name: str, WithDecryption: bool) -> dict[str, Any]:  # noqa: N803
            return {"Parameter": {"Value": "SET_ME"}}

    s3 = FakeS3()
    with pytest.raises(RuntimeError, match="placeholder"):
        _run(s3, _store(), FakeWeb(), ssm=PlaceholderSSM())
    assert s3.objects == {}


@pytest.mark.unit
def test_held_lock_blocks_a_second_run() -> None:
    s3 = FakeS3()
    s3.objects[f"{STATE_KEY}.lock"] = (b"other", NOW - timedelta(minutes=5))
    with pytest.raises(LockHeldError):
        _run(s3, _store(), FakeWeb())
    assert s3.uploads == 0


@pytest.mark.unit
def test_stale_lock_is_taken_over() -> None:
    s3 = FakeS3()
    s3.objects[f"{STATE_KEY}.lock"] = (b"crashed", NOW - timedelta(hours=2))
    result = _run(s3, _store(), FakeWeb())
    assert result["refresh"]["written"] == 3
    assert f"{STATE_KEY}.lock" not in s3.objects


@pytest.mark.unit
def test_failure_still_uploads_state_and_releases_lock() -> None:
    s3 = FakeS3()

    def broken_github(settings: Any, cache: Any) -> Any:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _run(s3, _store(), FakeWeb(), make_github=broken_github)
    assert STATE_KEY in s3.objects
    assert f"{STATE_KEY}.lock" not in s3.objects


@pytest.mark.unit
def test_download_errors_other_than_missing_propagate() -> None:
    class DeniedS3(FakeS3):
        def download_file(self, bucket: str, key: str, path: str) -> None:
            raise _client_error("403")

    s3 = DeniedS3()
    with pytest.raises(ClientError):
        _run(s3, _store(), FakeWeb())
    # Never overwrite remote state we couldn't read.
    assert s3.uploads == 0
    assert f"{STATE_KEY}.lock" not in s3.objects


@pytest.mark.unit
def test_s3_state_lock_round_trip() -> None:
    s3 = FakeS3()
    state = S3State(s3, "bucket", STATE_KEY)
    state.acquire_lock("run-1", NOW)
    with pytest.raises(LockHeldError):
        state.acquire_lock("run-2", NOW)
    state.release_lock()
    state.acquire_lock("run-2", NOW)
