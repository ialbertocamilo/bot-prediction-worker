"""
Centralized rate limiter for provider HTTP requests.

Features:
- Configurable requests-per-second per provider
- Retry with exponential backoff + jitter on HTTP 429
- Concurrency control via threading lock
- Shared across all provider clients
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ── defaults ──────────────────────────────────────────────────────────────

_DEFAULT_MIN_INTERVAL = 2.0       # seconds between requests
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_BASE = 2.0       # exponential base
_DEFAULT_BACKOFF_MAX = 60.0       # cap for backoff
_DEFAULT_JITTER_MAX = 1.0         # random jitter up to this many seconds


class RateLimiter:
    """Per-provider rate limiter with retry and backoff.

    Usage:
        limiter = RateLimiter("espn-scraper", min_interval=1.5)
        data = limiter.get("https://...", headers={...}, timeout=20)
    """

    def __init__(
        self,
        provider_name: str,
        min_interval: float = _DEFAULT_MIN_INTERVAL,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_base: float = _DEFAULT_BACKOFF_BASE,
        backoff_max: float = _DEFAULT_BACKOFF_MAX,
        jitter_max: float = _DEFAULT_JITTER_MAX,
        session: requests.Session | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.jitter_max = jitter_max
        self._session = session or requests.Session()
        self._lock = threading.Lock()
        self._metrics_lock = threading.Lock()
        self._last_request_time: float = 0.0

        # Metrics
        self.total_requests: int = 0
        self.total_retries: int = 0
        self.total_429s: int = 0

    def _throttle(self) -> None:
        """Enforce minimum interval between requests.

        Must be called while holding self._lock.
        """
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self.min_interval:
            wait = self.min_interval - elapsed
            logger.debug("%s: rate-limit wait %.2fs", self.provider_name, wait)
            time.sleep(wait)
        self._last_request_time = time.monotonic()

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 20,
    ) -> requests.Response:
        """HTTP GET with rate limiting, retry, and backoff."""
        return self._request("GET", url, params=params, headers=headers, timeout=timeout)

    def _request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 20,
    ) -> requests.Response:
        last_exc: Exception | None = None

        for attempt in range(self.max_retries + 1):
            # Hold lock only for throttle calculation
            with self._lock:
                self._throttle()
                self.total_requests += 1

            logger.debug("%s: %s %s (attempt %d)", self.provider_name, method, url, attempt + 1)

            try:
                resp = self._session.request(
                    method, url, params=params, headers=headers, timeout=timeout,
                )

                if resp.status_code == 429:
                    with self._metrics_lock:
                        self.total_429s += 1
                    retry_after = self._parse_retry_after(resp)
                    wait = retry_after or self._backoff_wait(attempt)
                    logger.warning(
                        "%s: HTTP 429 on %s — waiting %.1fs (attempt %d/%d)",
                        self.provider_name, url, wait, attempt + 1, self.max_retries + 1,
                    )
                    if attempt < self.max_retries:
                        with self._metrics_lock:
                            self.total_retries += 1
                        time.sleep(wait)
                        continue
                    resp.raise_for_status()

                if resp.status_code >= 500 and attempt < self.max_retries:
                    wait = self._backoff_wait(attempt)
                    logger.warning(
                        "%s: HTTP %d on %s — retrying in %.1fs",
                        self.provider_name, resp.status_code, url, wait,
                    )
                    with self._metrics_lock:
                        self.total_retries += 1
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                return resp

            except requests.exceptions.ConnectionError as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    wait = self._backoff_wait(attempt)
                    logger.warning(
                        "%s: connection error on %s — retrying in %.1fs",
                        self.provider_name, url, wait,
                    )
                    with self._metrics_lock:
                        self.total_retries += 1
                    time.sleep(wait)
                    continue
                raise

            except requests.exceptions.Timeout as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    wait = self._backoff_wait(attempt)
                    logger.warning(
                        "%s: timeout on %s — retrying in %.1fs",
                        self.provider_name, url, wait,
                    )
                    with self._metrics_lock:
                        self.total_retries += 1
                    time.sleep(wait)
                    continue
                raise

        raise last_exc or RuntimeError(f"{self.provider_name}: all retries exhausted for {url}")

    def _backoff_wait(self, attempt: int) -> float:
        """Exponential backoff with jitter."""
        base_wait = min(self.backoff_base ** attempt, self.backoff_max)
        jitter = random.uniform(0, self.jitter_max)  # noqa: S311
        return base_wait + jitter

    @staticmethod
    def _parse_retry_after(resp: requests.Response) -> float | None:
        """Parse Retry-After header if present."""
        header = resp.headers.get("Retry-After")
        if header is None:
            return None
        try:
            return float(header)
        except ValueError:
            return None

    def get_metrics(self) -> dict[str, Any]:
        """Return current metrics snapshot."""
        return {
            "provider": self.provider_name,
            "total_requests": self.total_requests,
            "total_retries": self.total_retries,
            "total_429s": self.total_429s,
        }


# ── Global registry of rate limiters ──────────────────────────────────────

_limiters: dict[str, RateLimiter] = {}
_registry_lock = threading.Lock()


def get_rate_limiter(
    provider_name: str,
    min_interval: float = _DEFAULT_MIN_INTERVAL,
    session: requests.Session | None = None,
) -> RateLimiter:
    """Get or create a rate limiter for a provider (singleton per name)."""
    with _registry_lock:
        if provider_name not in _limiters:
            _limiters[provider_name] = RateLimiter(
                provider_name=provider_name,
                min_interval=min_interval,
                session=session,
            )
        return _limiters[provider_name]


def get_all_metrics() -> list[dict[str, Any]]:
    """Return metrics for all registered rate limiters."""
    with _registry_lock:
        return [rl.get_metrics() for rl in _limiters.values()]


# ── Async rate limiter (for httpx) ────────────────────────────────────────

import asyncio

import httpx


class AsyncRateLimiter:
    """Per-provider async rate limiter with retry and backoff.

    Uses httpx.AsyncClient and asyncio primitives — fully non-blocking.

    Usage:
        limiter = AsyncRateLimiter("espn-scraper", min_interval=1.5)
        data = await limiter.get("https://...", headers={...})
    """

    def __init__(
        self,
        provider_name: str,
        min_interval: float = _DEFAULT_MIN_INTERVAL,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_base: float = _DEFAULT_BACKOFF_BASE,
        backoff_max: float = _DEFAULT_BACKOFF_MAX,
        jitter_max: float = _DEFAULT_JITTER_MAX,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.jitter_max = jitter_max
        self._client = client  # lazily created if None
        self._owns_client = client is None
        self._lock = asyncio.Lock()
        self._last_request_time: float = 0.0

        # Metrics
        self.total_requests: int = 0
        self.total_retries: int = 0
        self.total_429s: int = 0

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(20.0, connect=10.0),
                follow_redirects=True,
            )
            self._owns_client = True
        return self._client

    async def _throttle(self) -> None:
        """Enforce minimum interval between requests (non-blocking)."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self.min_interval:
            await asyncio.sleep(self.min_interval - elapsed)
        self._last_request_time = time.monotonic()

    async def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 20.0,
    ) -> httpx.Response:
        """Async HTTP GET with rate limiting, retry, and backoff."""
        client = await self._ensure_client()
        last_exc: Exception | None = None

        for attempt in range(self.max_retries + 1):
            async with self._lock:
                await self._throttle()
                self.total_requests += 1

            logger.debug(
                "%s: GET %s (attempt %d)", self.provider_name, url, attempt + 1,
            )

            try:
                resp = await client.get(
                    url, params=params, headers=headers, timeout=timeout,
                )

                if resp.status_code == 429:
                    self.total_429s += 1
                    retry_after = self._parse_retry_after(resp)
                    wait = retry_after or self._backoff_wait(attempt)
                    logger.warning(
                        "%s: HTTP 429 on %s — waiting %.1fs (attempt %d/%d)",
                        self.provider_name, url, wait,
                        attempt + 1, self.max_retries + 1,
                    )
                    if attempt < self.max_retries:
                        self.total_retries += 1
                        await asyncio.sleep(wait)
                        continue
                    resp.raise_for_status()

                if resp.status_code >= 500 and attempt < self.max_retries:
                    wait = self._backoff_wait(attempt)
                    logger.warning(
                        "%s: HTTP %d on %s — retrying in %.1fs",
                        self.provider_name, resp.status_code, url, wait,
                    )
                    self.total_retries += 1
                    await asyncio.sleep(wait)
                    continue

                resp.raise_for_status()
                return resp

            except httpx.ConnectError as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    wait = self._backoff_wait(attempt)
                    logger.warning(
                        "%s: connect error on %s — retrying in %.1fs",
                        self.provider_name, url, wait,
                    )
                    self.total_retries += 1
                    await asyncio.sleep(wait)
                    continue
                raise

            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    wait = self._backoff_wait(attempt)
                    logger.warning(
                        "%s: timeout on %s — retrying in %.1fs",
                        self.provider_name, url, wait,
                    )
                    self.total_retries += 1
                    await asyncio.sleep(wait)
                    continue
                raise

        raise last_exc or RuntimeError(
            f"{self.provider_name}: all retries exhausted for {url}",
        )

    def _backoff_wait(self, attempt: int) -> float:
        base_wait = min(self.backoff_base ** attempt, self.backoff_max)
        jitter = random.uniform(0, self.jitter_max)  # noqa: S311
        return base_wait + jitter

    @staticmethod
    def _parse_retry_after(resp: httpx.Response) -> float | None:
        header = resp.headers.get("Retry-After")
        if header is None:
            return None
        try:
            return float(header)
        except ValueError:
            return None

    async def close(self) -> None:
        if self._owns_client and self._client and not self._client.is_closed:
            await self._client.aclose()

    def get_metrics(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "total_requests": self.total_requests,
            "total_retries": self.total_retries,
            "total_429s": self.total_429s,
        }


# ── Global registry for async rate limiters ──────────────────────────────

_async_limiters: dict[str, AsyncRateLimiter] = {}
_async_registry_lock = threading.Lock()


def get_async_rate_limiter(
    provider_name: str,
    min_interval: float = _DEFAULT_MIN_INTERVAL,
) -> AsyncRateLimiter:
    """Get or create an async rate limiter for a provider (singleton per name)."""
    with _async_registry_lock:
        if provider_name not in _async_limiters:
            _async_limiters[provider_name] = AsyncRateLimiter(
                provider_name=provider_name,
                min_interval=min_interval,
            )
        return _async_limiters[provider_name]
