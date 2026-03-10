from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)


class EspnScraperClient:
    """Cliente para los endpoints públicos de ESPN Soccer API.

    Estos endpoints devuelven JSON directamente (no requieren scraping HTML).
    No necesitan API key. Rate-limit: ser conservador (~1 req/seg).

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

    def __init__(self, league_slug: str | None = None) -> None:
        self.league_slug: str = league_slug or os.getenv("ESPN_LEAGUE_SLUG", "per.1")
        self._last_request_time: float = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self._MIN_INTERVAL:
            wait = self._MIN_INTERVAL - elapsed
            logger.debug("ESPN scraper: esperando %.1fs por rate-limit", wait)
            time.sleep(wait)

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._throttle()
        response: requests.Response = requests.get(
            url,
            params=params or {},
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; SoccerBot/1.0)",
                "Accept": "application/json",
            },
            timeout=20,
        )
        self._last_request_time = time.monotonic()
        response.raise_for_status()
        return response.json()

    # ── Scoreboard (partidos de una fecha) ──────────────────────

    def get_scoreboard(self, target_date: date) -> dict[str, Any]:
        """GET /{league_slug}/scoreboard?dates=YYYYMMDD
        Devuelve partidos de una fecha específica."""
        return self._get(
            f"{self.BASE_URL}/{self.league_slug}/scoreboard",
            params={"dates": target_date.strftime("%Y%m%d")},
        )

    # ── Fixtures y Resultados (rango de fechas) ────────────────

    def get_matches_in_range(
        self,
        date_from: date,
        date_to: date,
    ) -> list[dict[str, Any]]:
        """Obtiene partidos en un rango de fechas.
        ESPN solo retorna una fecha por request, así que iteramos
        sobre las fechas del calendario de la liga."""
        # Primero obtenemos el calendario de la liga
        first_day = self.get_scoreboard(date_from)
        calendar_dates: list[str] = []

        for league in first_day.get("leagues", []):
            calendar_dates = league.get("calendar", [])
            break

        # Filtrar solo las fechas del calendario que caen en nuestro rango
        target_dates: list[date] = []
        for cal_date_str in calendar_dates:
            try:
                cal_date = date.fromisoformat(cal_date_str[:10])
                if date_from <= cal_date <= date_to:
                    target_dates.append(cal_date)
            except (ValueError, IndexError):
                continue

        # Si no hay calendario, iterar día a día (fallback)
        if not target_dates:
            current = date_from
            while current <= date_to:
                target_dates.append(current)
                current += timedelta(days=1)

        all_events: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        # Ya tenemos los datos del primer día
        for event in first_day.get("events", []):
            eid = event.get("id", "")
            if eid not in seen_ids:
                all_events.append(event)
                seen_ids.add(eid)

        for target in target_dates:
            if target == date_from:
                continue  # ya lo tenemos
            try:
                data = self.get_scoreboard(target)
                for event in data.get("events", []):
                    eid = event.get("id", "")
                    if eid not in seen_ids:
                        all_events.append(event)
                        seen_ids.add(eid)
            except Exception as e:
                logger.warning("ESPN scraper: error en fecha %s: %s", target, e)
                continue

        logger.info(
            "ESPN scraper: %d partidos obtenidos (%s → %s)",
            len(all_events), date_from, date_to,
        )
        return all_events

    # ── Teams (desde standings) ─────────────────────────────────

    def get_teams(self) -> dict[str, Any]:
        """GET /{league_slug}/standings — devuelve equipos con datos."""
        return self._get(
            f"{self.BASE_URL}/{self.league_slug}/standings",
        )

    # ── League info ─────────────────────────────────────────────

    def get_league_info(self) -> dict[str, Any]:
        """Obtiene info de la liga desde el scoreboard actual."""
        return self.get_scoreboard(date.today())
