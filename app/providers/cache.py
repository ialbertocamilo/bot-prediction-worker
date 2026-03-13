"""
TTL-based LRU cache for provider API responses.

Avoids redundant HTTP requests when the same data is fetched
within a short time window (e.g., fixtures for the same league/date).

Thread-safe.  Keys are derived from (provider, endpoint, params).
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_TTL = 900          # 15 minutes
_DEFAULT_MAX_ENTRIES = 500
_PURGE_INTERVAL = 300       # purge expired entries every 5 minutes


class ProviderCache:
    """In-memory TTL + LRU cache for provider responses."""

    def __init__(
        self,
        ttl: int = _DEFAULT_TTL,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
    ) -> None:
        self.ttl = ttl
        self.max_entries = max_entries
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self._last_purge: float = time.monotonic()

        # Metrics
        self.hits: int = 0
        self.misses: int = 0

    @staticmethod
    def make_key(provider_name: str, endpoint: str, params: dict[str, Any] | None = None) -> str:
        """Build a deterministic cache key."""
        raw = f"{provider_name}:{endpoint}:{json.dumps(params or {}, sort_keys=True)}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, key: str) -> Any | None:
        """Return cached value if present and not expired, else None."""
        with self._lock:
            self._maybe_purge_expired()

            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None

            ts, value = entry
            if time.monotonic() - ts > self.ttl:
                # Expired — evict
                del self._store[key]
                self.misses += 1
                return None

            # Move to end (most recently used)
            self._store.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: str, value: Any) -> None:
        """Store a value with the current timestamp."""
        with self._lock:
            self._maybe_purge_expired()

            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (time.monotonic(), value)
            self._evict()

    def invalidate(self, key: str) -> None:
        """Remove a specific key."""
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        """Remove all entries."""
        with self._lock:
            self._store.clear()

    def _evict(self) -> None:
        """Remove oldest entries if over max_entries."""
        while len(self._store) > self.max_entries:
            self._store.popitem(last=False)

    def _maybe_purge_expired(self) -> None:
        """Remove all expired entries if enough time has passed since last purge.

        Must be called while holding self._lock.
        """
        now = time.monotonic()
        if now - self._last_purge < _PURGE_INTERVAL:
            return
        self._last_purge = now
        expired = [
            k for k, (ts, _) in self._store.items()
            if now - ts > self.ttl
        ]
        for k in expired:
            del self._store[k]
        if expired:
            logger.debug("ProviderCache: purged %d expired entries", len(expired))

    def purge_expired(self) -> int:
        """Manually purge all expired entries. Returns count removed."""
        with self._lock:
            now = time.monotonic()
            expired = [
                k for k, (ts, _) in self._store.items()
                if now - ts > self.ttl
            ]
            for k in expired:
                del self._store[k]
            self._last_purge = now
            return len(expired)

    def get_metrics(self) -> dict[str, Any]:
        """Return cache metrics."""
        with self._lock:
            return {
                "size": len(self._store),
                "max_entries": self.max_entries,
                "ttl": self.ttl,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / max(self.hits + self.misses, 1), 3),
            }


# ── Singleton ─────────────────────────────────────────────────────────────

_cache: ProviderCache | None = None
_cache_lock = threading.Lock()


def get_provider_cache(
    ttl: int = _DEFAULT_TTL,
    max_entries: int = _DEFAULT_MAX_ENTRIES,
) -> ProviderCache:
    """Get the global provider cache (singleton)."""
    global _cache
    with _cache_lock:
        if _cache is None:
            _cache = ProviderCache(ttl=ttl, max_entries=max_entries)
        return _cache
