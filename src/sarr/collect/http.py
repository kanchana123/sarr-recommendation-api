"""Polite JSON GET client: throttling, rate-limit headers, retries, ETag caching."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

logger = logging.getLogger("sarr.collect")

RETRYABLE_STATUS = {500, 502, 503, 504}


class ResponseCache(Protocol):
    def get(self, key: str) -> tuple[str, Any] | None: ...

    def put(self, key: str, etag: str, data: Any) -> None: ...


class RetriesExhaustedError(RuntimeError):
    pass


class BudgetExhaustedError(RuntimeError):
    """Raised instead of sleeping past the run deadline."""


@dataclass
class FetchResult:
    status: int
    data: Any = None
    from_cache: bool = False
    redirected: bool = False


class PoliteClient:
    def __init__(
        self,
        *,
        user_agent: str,
        headers: dict[str, str] | None = None,
        min_interval_s: float = 0.0,
        max_retries: int = 4,
        backoff_base_s: float = 1.0,
        max_sleep_s: float = 3700.0,
        low_remaining: int = 50,
        cache: ResponseCache | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = 20.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self.http = httpx.Client(
            headers={"User-Agent": user_agent, **(headers or {})},
            timeout=timeout_s,
            follow_redirects=True,
            transport=transport,
        )
        self.min_interval_s = min_interval_s
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.max_sleep_s = max_sleep_s
        self.low_remaining = low_remaining
        self.cache = cache
        self.deadline: float | None = None
        self._sleep_fn = sleep
        self._clock = clock
        self._rng = rng
        self._last_request_at: float | None = None

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        parse: Callable[[Any], Any] = lambda body: body,
    ) -> FetchResult:
        """GET url; `parse` shrinks the JSON body to what callers (and the cache) keep."""
        key = f"{url}?{urlencode(sorted(params.items()))}" if params else url
        cached = self.cache.get(key) if self.cache else None
        last_status: int | None = None

        for attempt in range(self.max_retries + 1):
            self._throttle()
            headers = {"If-None-Match": cached[0]} if cached else {}
            try:
                response = self.http.get(url, params=params, headers=headers)
            except httpx.TransportError as exc:
                logger.warning("transport error url=%s attempt=%s error=%s", url, attempt, exc)
                last_status = None
                if attempt < self.max_retries:
                    self._sleep(self._backoff(attempt))
                continue

            last_status = response.status_code
            self._respect_rate_limit(response)

            if response.status_code == 304 and cached:
                return FetchResult(status=200, data=cached[1], from_cache=True)

            wait = self._throttled_wait(response, attempt)
            if wait is None and response.status_code in RETRYABLE_STATUS:
                wait = self._backoff(attempt)
            if wait is not None:
                if attempt < self.max_retries:
                    logger.warning(
                        "retrying url=%s status=%s sleeping=%.1fs",
                        url,
                        response.status_code,
                        wait,
                    )
                    self._sleep(wait)
                continue

            redirected = any(r.status_code in (301, 308) for r in response.history)
            if response.status_code != 200:
                return FetchResult(status=response.status_code, redirected=redirected)

            data = parse(response.json())
            etag = response.headers.get("ETag")
            if self.cache and etag:
                self.cache.put(key, etag, data)
            return FetchResult(status=200, data=data, redirected=redirected)

        raise RetriesExhaustedError(f"GET {url} failed after retries (last status {last_status})")

    def close(self) -> None:
        self.http.close()

    def _throttled_wait(self, response: httpx.Response, attempt: int) -> float | None:
        status = response.status_code
        retry_after = response.headers.get("Retry-After")
        if status == 429 or (status == 403 and retry_after):
            if retry_after and retry_after.isdigit():
                return float(retry_after)
            return self._reset_wait(response) or self._backoff(attempt)
        if status == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
            return self._reset_wait(response) or self._backoff(attempt)
        return None

    def _respect_rate_limit(self, response: httpx.Response) -> None:
        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining is None or not remaining.isdigit():
            return
        # Remaining == 0 on a 403 is handled as a retry by _throttled_wait.
        if 0 < int(remaining) < self.low_remaining:
            wait = self._reset_wait(response)
            if wait:
                logger.info("rate limit low remaining=%s sleeping=%.0fs", remaining, wait)
                self._sleep(wait)

    def _reset_wait(self, response: httpx.Response) -> float | None:
        reset = response.headers.get("X-RateLimit-Reset")
        if reset is None or not reset.isdigit():
            return None
        return max(float(reset) - self._clock(), 0.0) + 1.0

    def _backoff(self, attempt: int) -> float:
        return min(self.backoff_base_s * (2**attempt) * (0.5 + self._rng()), 60.0)

    def _throttle(self) -> None:
        if self._last_request_at is not None and self.min_interval_s > 0:
            elapsed = self._clock() - self._last_request_at
            if elapsed < self.min_interval_s:
                self._sleep(self.min_interval_s - elapsed)
        self._last_request_at = self._clock()

    def _sleep(self, seconds: float) -> None:
        seconds = min(seconds, self.max_sleep_s)
        if self.deadline is not None and self._clock() + seconds > self.deadline:
            raise BudgetExhaustedError(f"sleeping {seconds:.0f}s would pass the run deadline")
        self._sleep_fn(seconds)
