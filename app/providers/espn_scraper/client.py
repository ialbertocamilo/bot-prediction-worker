from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, timedelta
from typing import Any

import httpx
import requests

from app.providers.cache import ProviderCache, get_provider_cache
from app.providers.rate_limiter import (
    AsyncRateLimiter,
    RateLimiter,
    get_async_rate_limiter,
    get_rate_limiter,
)

logger = logging.getLogger(__name__)

# Volatile per-process cache: (league_slug, "YYYYMMDD") combos already
# fetched this run that returned zero events.  Avoids re-requesting dates
# that are known-empty for a given league during the current execution.
_empty_dates: set[tuple[str, str]] = set()

_HEADERS: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (compatible; SoccerBot/1.0)",
    "Accept": "application/json",
}


def _parse_calendar_dates(
    calendar_entries: list[Any],
    date_from: date,
    date_to: date,
) -> list[date]:
    """Extract target dates from ESPN calendar entries within [date_from, date_to]."""
    target_dates: list[date] = []
    for cal_entry in calendar_entries:
        try:
            if isinstance(cal_entry, str):
                raw = cal_entry[:10]
            elif isinstance(cal_entry, dict):
                raw = cal_entry.get("startDate", cal_entry.get("date", ""))[:10]
                if not raw:
                    for e in cal_entry.get("entries", []):
                        sd = e.get("startDate", "")[:10]
                        if sd:
                            try:
                                cd = date.fromisoformat(sd)
                                if date_from <= cd <= date_to and cd not in target_dates:
                                    target_dates.append(cd)
                            except (ValueError, IndexError):
                                pass
                    continue
            else:
                continue
            cal_date = date.fromisoformat(raw)
            if date_from <= cal_date <= date_to:
                target_dates.append(cal_date)
        except (ValueError, IndexError, TypeError):
            continue
    return target_dates


def _collect_events(
    data: dict[str, Any],
    seen_ids: set[str],
    all_events: list[dict[str, Any]],
) -> None:
    """Append new events from a scoreboard response, deduplicating by id."""
    for event in data.get("events", []):
        eid = event.get("id", "")
        if eid and eid not in seen_ids:
            all_events.append(event)
            seen_ids.add(eid)


class EspnScraperClient:
    """Cliente para los endpoints públicos de ESPN Soccer API.

    Soporta tanto acceso síncrono (scripts/CLI) como asíncrono (bot).
    Los métodos async usan httpx + AsyncRateLimiter con concurrencia controlada.

    Slugs de ligas:
        per.1   = Liga 1 Perú
        arg.1   = Liga Profesional Argentina
        bra.1   = Brasileirão Série A
        col.1   = Liga BetPlay Colombia
        ecu.1   = LigaPro Ecuador
        mex.1   = Liga MX
        usa.1   = MLS
        eng.1   = Premier League
        esp.1   = La Liga
        ita.1   = Serie A
        ger.1   = Bundesliga
        fra.1   = Ligue 1
        uefa.champions = Champions League
    """

    BASE_URL: str = "https://site.api.espn.com/apis/site/v2/sports/soccer"
    _MIN_INTERVAL: float = 1.5  # segundos entre requests
    _MAX_CONCURRENT: int = 5    # máx. requests en vuelo simultáneo

    def __init__(self, league_slug: str | None = None) -> None:
        self.league_slug: str = league_slug or os.getenv("ESPN_LEAGUE_SLUG", "per.1")
        # Sync (for scripts / CLI)
        self._limiter: RateLimiter = get_rate_limiter(
            "espn-scraper", min_interval=self._MIN_INTERVAL,
        )
        # Async (for bot)
        self._async_limiter: AsyncRateLimiter = get_async_rate_limiter(
            "espn-scraper-async", min_interval=self._MIN_INTERVAL,
        )
        self._cache: ProviderCache = get_provider_cache()
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(self._MAX_CONCURRENT)

    # ══════════════════════════════════════════════════════════════
    # SYNC methods (kept for scripts, CLI, and other sync callers)
    # ══════════════════════════════════════════════════════════════

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        cache_key = self._cache.make_key("espn-scraper", url, params)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("ESPN cache hit: %s", url)
            return cached

        resp = self._limiter.get(
            url, params=params, headers=_HEADERS, timeout=20,
        )
        data = resp.json()
        self._cache.set(cache_key, data)
        return data

    def get_scoreboard(self, target_date: date) -> dict[str, Any]:
        """GET /{league_slug}/scoreboard?dates=YYYYMMDD (sync)."""
        return self._get(
            f"{self.BASE_URL}/{self.league_slug}/scoreboard",
            params={"dates": target_date.strftime("%Y%m%d")},
        )

    def get_matches_in_range(
        self, date_from: date, date_to: date,
    ) -> list[dict[str, Any]]:
        """Fetch matches in date range — sequential sync (for scripts)."""
        first_day = self.get_scoreboard(date_from)
        calendar: list[Any] = []
        for league in first_day.get("leagues", []):
            calendar = league.get("calendar", [])
            break

        target_dates = _parse_calendar_dates(calendar, date_from, date_to)
        if not target_dates:
            current = date_from
            while current <= date_to:
                target_dates.append(current)
                current += timedelta(days=1)

        all_events: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        _collect_events(first_day, seen_ids, all_events)

        for target in target_dates:
            if target == date_from:
                continue

            cache_key = (self.league_slug, target.strftime("%Y%m%d"))
            if cache_key in _empty_dates:
                logger.debug("ESPN sync: skipping known-empty %s/%s", self.league_slug, target)
                continue

            try:
                data = self.get_scoreboard(target)
                events = data.get("events", [])
                if not events:
                    _empty_dates.add(cache_key)
                else:
                    _collect_events(data, seen_ids, all_events)
            except Exception as e:
                logger.warning("ESPN scraper: error en fecha %s: %s", target, e)

        logger.info(
            "ESPN scraper (sync): %d partidos (%s → %s)",
            len(all_events), date_from, date_to,
        )
        return all_events

    def get_teams(self) -> dict[str, Any]:
        return self._get(f"{self.BASE_URL}/{self.league_slug}/standings")

    def get_league_info(self) -> dict[str, Any]:
        return self.get_scoreboard(date.today())

    # ══════════════════════════════════════════════════════════════
    # ASYNC methods (httpx + AsyncRateLimiter + asyncio.gather)
    # ══════════════════════════════════════════════════════════════

    async def _aget(
        self, url: str, params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Async HTTP GET with caching and rate limiting."""
        cache_key = self._cache.make_key("espn-scraper", url, params)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("ESPN async cache hit: %s", url)
            return cached

        async with self._semaphore:
            resp = await self._async_limiter.get(
                url, params=params, headers=_HEADERS,
            )
        data = resp.json()
        self._cache.set(cache_key, data)
        return data

    async def aget_scoreboard(self, target_date: date) -> dict[str, Any]:
        """GET /{league_slug}/scoreboard?dates=YYYYMMDD (async)."""
        return await self._aget(
            f"{self.BASE_URL}/{self.league_slug}/scoreboard",
            params={"dates": target_date.strftime("%Y%m%d")},
        )

    async def aget_matches_in_range(
        self, date_from: date, date_to: date,
    ) -> list[dict[str, Any]]:
        """Fetch matches in date range — sequential with throttle.

        1. Fetch the first day to get the calendar.
        2. Parse calendar to find target dates.
        3. Fetch remaining dates one-by-one with 1.5 s delay between
           requests to be friendly to ESPN servers.
        4. Skip dates already known-empty from previous runs.
        """
        first_day = await self.aget_scoreboard(date_from)
        calendar: list[Any] = []
        for league in first_day.get("leagues", []):
            calendar = league.get("calendar", [])
            break

        target_dates = _parse_calendar_dates(calendar, date_from, date_to)
        if not target_dates:
            current = date_from
            while current <= date_to:
                target_dates.append(current)
                current += timedelta(days=1)

        all_events: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        _collect_events(first_day, seen_ids, all_events)

        for target in target_dates:
            if target == date_from:
                continue

            cache_key = (self.league_slug, target.strftime("%Y%m%d"))
            if cache_key in _empty_dates:
                logger.debug("ESPN async: skipping known-empty %s/%s", self.league_slug, target)
                continue

            await asyncio.sleep(1.5)  # throttle between requests

            try:
                data = await self.aget_scoreboard(target)
                events = data.get("events", [])
                if not events:
                    _empty_dates.add(cache_key)
                else:
                    _collect_events(data, seen_ids, all_events)
            except httpx.HTTPError as exc:
                logger.warning("ESPN async: HTTP error on %s: %s", target, exc)
            except Exception as exc:
                logger.warning("ESPN async: error on %s: %s", target, exc)

        logger.info(
            "ESPN scraper (async): %d partidos (%s → %s)",
            len(all_events), date_from, date_to,
        )
        return all_events

    async def aget_teams(self) -> dict[str, Any]:
        return await self._aget(f"{self.BASE_URL}/{self.league_slug}/standings")

    async def aget_league_info(self) -> dict[str, Any]:
        return await self.aget_scoreboard(date.today())

    async def close(self) -> None:
        """Close the underlying async rate limiter's httpx client."""
        await self._async_limiter.close()
