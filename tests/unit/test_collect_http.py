"""PoliteClient: rate limits, Retry-After, backoff, ETag conditional requests."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from sarr.collect.http import BudgetExhaustedError, PoliteClient, RetriesExhaustedError


class MemoryCache:
    def __init__(self) -> None:
        self.store: dict[str, tuple[str, Any]] = {}

    def get(self, key: str) -> tuple[str, Any] | None:
        return self.store.get(key)

    def put(self, key: str, etag: str, data: Any) -> None:
        self.store[key] = (etag, data)


class FakeTime:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _client(handler: Any, fake: FakeTime, **kwargs: Any) -> PoliteClient:
    return PoliteClient(
        user_agent="sarr-test",
        transport=httpx.MockTransport(handler),
        sleep=fake.sleep,
        clock=fake.clock,
        rng=lambda: 0.5,
        **kwargs,
    )


@pytest.mark.unit
def test_sends_user_agent_and_returns_parsed_json() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"a": 1, "b": 2})

    result = _client(handler, FakeTime()).get_json("https://x.test/r", parse=lambda b: b["a"])
    assert result.status == 200 and result.data == 1
    assert seen[0].headers["User-Agent"] == "sarr-test"


@pytest.mark.unit
def test_429_honors_retry_after_then_succeeds() -> None:
    responses = iter(
        [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json={"ok": True})]
    )
    fake = FakeTime()
    result = _client(lambda r: next(responses), fake).get_json("https://x.test/r")
    assert result.data == {"ok": True}
    assert fake.sleeps == [7.0]


@pytest.mark.unit
def test_429_without_retry_after_uses_exponential_backoff() -> None:
    responses = iter(
        [httpx.Response(429), httpx.Response(429), httpx.Response(200, json={})]
    )
    fake = FakeTime()
    _client(lambda r: next(responses), fake, backoff_base_s=1.0).get_json("https://x.test/r")
    # base * 2**attempt * (0.5 + jitter 0.5)
    assert fake.sleeps == [1.0, 2.0]


@pytest.mark.unit
def test_403_with_exhausted_quota_waits_until_reset() -> None:
    fake = FakeTime()
    reset = int(fake.now) + 120
    responses = iter(
        [
            httpx.Response(
                403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)}
            ),
            httpx.Response(200, json={}),
        ]
    )
    _client(lambda r: next(responses), fake).get_json("https://x.test/r")
    assert fake.sleeps == [121.0]


@pytest.mark.unit
def test_low_remaining_quota_sleeps_before_next_request() -> None:
    fake = FakeTime()
    reset = int(fake.now) + 60
    headers = {"X-RateLimit-Remaining": "10", "X-RateLimit-Reset": str(reset)}
    result = _client(lambda r: httpx.Response(200, json={}, headers=headers), fake).get_json(
        "https://x.test/r"
    )
    assert result.status == 200
    assert fake.sleeps == [61.0]


@pytest.mark.unit
def test_5xx_retries_then_raises_when_exhausted() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    fake = FakeTime()
    with pytest.raises(RetriesExhaustedError):
        _client(handler, fake, max_retries=2).get_json("https://x.test/r")
    assert calls == 3
    assert fake.sleeps == [1.0, 2.0]


@pytest.mark.unit
def test_transport_errors_are_retried() -> None:
    attempts = iter([httpx.ConnectError("boom"), httpx.Response(200, json={"ok": 1})])

    def handler(request: httpx.Request) -> httpx.Response:
        item = next(attempts)
        if isinstance(item, Exception):
            raise item
        return item

    result = _client(handler, FakeTime()).get_json("https://x.test/r")
    assert result.data == {"ok": 1}


@pytest.mark.unit
def test_404_is_returned_not_retried() -> None:
    fake = FakeTime()
    result = _client(lambda r: httpx.Response(404), fake).get_json("https://x.test/r")
    assert result.status == 404 and result.data is None
    assert fake.sleeps == []


@pytest.mark.unit
def test_etag_conditional_request_serves_304_from_cache() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("If-None-Match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, json={"stars": 5}, headers={"ETag": '"v1"'})

    client = _client(handler, FakeTime(), cache=MemoryCache())
    first = client.get_json("https://x.test/r", params={"per_page": 5})
    second = client.get_json("https://x.test/r", params={"per_page": 5})
    assert first.data == second.data == {"stars": 5}
    assert not first.from_cache and second.from_cache
    assert "If-None-Match" not in seen[0].headers
    assert seen[1].headers["If-None-Match"] == '"v1"'


@pytest.mark.unit
def test_min_interval_spaces_requests() -> None:
    fake = FakeTime()
    client = _client(lambda r: httpx.Response(200, json={}), fake, min_interval_s=0.5)
    client.get_json("https://x.test/a")
    client.get_json("https://x.test/b")
    assert fake.sleeps == [0.5]


@pytest.mark.unit
def test_sleep_past_deadline_raises_budget_exhausted() -> None:
    fake = FakeTime()
    client = _client(lambda r: httpx.Response(429, headers={"Retry-After": "600"}), fake)
    client.deadline = fake.now + 60
    with pytest.raises(BudgetExhaustedError):
        client.get_json("https://x.test/r")
    assert fake.sleeps == []


@pytest.mark.unit
def test_redirect_is_followed_and_flagged() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"Location": "https://x.test/new"})
        return httpx.Response(200, json={"name": "new"})

    result = _client(handler, FakeTime()).get_json("https://x.test/old")
    assert result.redirected and result.data == {"name": "new"}


@pytest.mark.unit
def test_retry_after_zero_still_backs_off() -> None:
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={}),
        ]
    )
    fake = FakeTime()
    _client(lambda r: next(responses), fake, backoff_base_s=1.0).get_json("https://x.test/r")
    assert fake.sleeps == [1.0, 2.0]
