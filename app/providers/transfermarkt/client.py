from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)


class TransfermarktClient:
    """Cliente HTTP para scraping de Transfermarkt.

    Obtiene datos de plantillas y jugadores parseando el HTML público.
    Transfermarkt requiere headers específicos (Accept-Language, User-Agent)
    para devolver la versión completa de la página.

    Configuración vía variables de entorno:
        TRANSFERMARKT_LEAGUE_PATH  – slug de la liga  (default: liga-1/startseite/wettbewerb/PE1)
    """

    BASE_URL: str = "https://www.transfermarkt.com"
    _MIN_INTERVAL: float = 3.0  # conservador — Transfermarkt bloquea rápido

    def __init__(self, league_path: str | None = None) -> None:
        self.league_path: str = league_path or os.getenv(
            "TRANSFERMARKT_LEAGUE_PATH",
            "liga-1/startseite/wettbewerb/PE1",
        )
        self._last_request_time: float = 0.0
        self._session: requests.Session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Referer": "https://www.transfermarkt.com/",
            }
        )

    # ── Throttle ────────────────────────────────────────────────

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self._MIN_INTERVAL:
            wait = self._MIN_INTERVAL - elapsed
            logger.debug("Transfermarkt: esperando %.1fs por rate-limit", wait)
            time.sleep(wait)

    def _get_html(self, path: str) -> BeautifulSoup:
        url = f"{self.BASE_URL}/{path}"
        self._throttle()
        logger.debug("Transfermarkt GET %s", url)
        resp = self._session.get(url, timeout=25)
        self._last_request_time = time.monotonic()
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")

    # ── Equipos de una liga ─────────────────────────────────────

    def get_teams_page(self, season_year: int) -> BeautifulSoup:
        """Obtiene la página principal de la liga con la lista de equipos.

        URL ejemplo:
          /liga-1/startseite/wettbewerb/PE1/plus/?saison_id=2026
        """
        path = f"{self.league_path}/plus/?saison_id={season_year}"
        return self._get_html(path)

    # ── Plantilla de un equipo ──────────────────────────────────

    def get_squad_page(self, team_path: str, season_year: int) -> BeautifulSoup:
        """Obtiene la tabla de plantilla de un equipo.

        ``team_path`` es el path relativo que incluye verein/{id}, e.g.
        ``universitario-de-deportes/kader/verein/3186``.

        URL ejemplo:
          /universitario-de-deportes/kader/verein/3186/saison_id/2026/plus/1
        """
        path = f"{team_path}/saison_id/{season_year}/plus/1"
        return self._get_html(path)

    # ── Parsing helpers ─────────────────────────────────────────

    @staticmethod
    def parse_team_links(soup: BeautifulSoup) -> list[dict[str, str]]:
        """Extrae links de equipos desde la tabla principal de la liga.

        Retorna lista de dicts con ``name`` y ``squad_path``.
        """
        teams: list[dict[str, str]] = []
        seen: set[str] = set()

        # Transfermarkt: los equipos están en <td class="hauptlink no-visu-visit">
        for td in soup.select("td.hauptlink.no-visu-visit"):
            anchor: Tag | None = td.find("a")
            if anchor is None:
                continue
            href: str = anchor.get("href", "")
            name: str = anchor.get_text(strip=True)
            if not href or not name or href in seen:
                continue

            # Convertir href de startseite a kader
            # /universitario-de-deportes/startseite/verein/3186/saison_id/2026
            # → universitario-de-deportes/kader/verein/3186
            squad_path = TransfermarktClient._to_squad_path(href)
            if squad_path:
                seen.add(href)
                teams.append({"name": name, "squad_path": squad_path})

        return teams

    @staticmethod
    def _to_squad_path(href: str) -> str | None:
        """Convierte un href de equipo a la ruta de plantilla (kader)."""
        # href típico: /club-slug/startseite/verein/1234/saison_id/2026
        parts = href.strip("/").split("/")
        try:
            verein_idx = parts.index("verein")
            club_slug = parts[0]
            verein_id = parts[verein_idx + 1]
            return f"{club_slug}/kader/verein/{verein_id}"
        except (ValueError, IndexError):
            return None

    @staticmethod
    def parse_squad_table(soup: BeautifulSoup) -> list[dict[str, Any]]:
        """Parsea la tabla de plantilla y retorna datos crudos de jugadores.

        Cada dict contiene claves: ``name``, ``external_id``, ``position``,
        ``date_of_birth``, ``nationality``, ``height_cm``, ``foot``,
        ``jersey_number``, ``market_value``, ``contract_until``.
        """
        players: list[dict[str, Any]] = []

        # La tabla principal de plantilla tiene class "items"
        table: Tag | None = soup.select_one("table.items")
        if table is None:
            return players

        tbody: Tag | None = table.find("tbody")
        if tbody is None:
            return players

        for row in tbody.find_all("tr", class_=["odd", "even"]):
            player = TransfermarktClient._parse_player_row(row)
            if player:
                players.append(player)

        return players

    @staticmethod
    def _parse_player_row(row: Tag) -> dict[str, Any] | None:
        """Parsea una fila <tr> de la tabla de plantilla."""
        cells = row.find_all("td")
        if len(cells) < 4:
            return None

        # Nombre y external_id
        name_cell: Tag | None = row.select_one("td.hauptlink a")
        if name_cell is None:
            return None

        name: str = name_cell.get_text(strip=True)
        href: str = name_cell.get("href", "")
        external_id: str | None = None
        if href:
            parts = href.strip("/").split("/")
            if parts:
                external_id = parts[-1]  # último segmento = spieler ID

        # Posición
        position_cell = row.select("td.posrela table td")
        position_raw: str = ""
        if len(position_cell) >= 2:
            position_raw = position_cell[-1].get_text(strip=True)

        # Dorsal
        jersey: int | None = None
        rn_cell = row.select_one("div.rn_nummer")
        if rn_cell:
            try:
                jersey = int(rn_cell.get_text(strip=True))
            except ValueError:
                pass

        # Fecha de nacimiento y nacionalidad
        dob: str | None = None
        nationality: str | None = None
        for cell in cells:
            text = cell.get_text(strip=True)
            # Fecha: formato "(XX) Mon DD, YYYY" o "DD/MM/YYYY" dependiendo del idioma
            if "(" in text and ")" in text and len(text) > 8:
                # Extraer solo la parte de fecha
                paren_start = text.rfind("(")
                if paren_start > 0:
                    dob = text[:paren_start].strip()
            # Nacionalidad: buscar img con class "flaggenrahmen"
            flag = cell.find("img", class_="flaggenrahmen")
            if flag:
                nationality = flag.get("title", flag.get("alt", ""))

        # Altura
        height_cm: int | None = None
        for cell in cells:
            text = cell.get_text(strip=True)
            if "m" in text and "," in text:
                try:
                    # "1,85 m" → 185
                    cleaned = text.replace("m", "").replace(",", "").strip()
                    height_cm = int(cleaned)
                except ValueError:
                    pass

        # Pie preferido
        foot: str | None = None
        for cell in cells:
            text_lower = cell.get_text(strip=True).lower()
            if text_lower in ("derecho", "right"):
                foot = "RIGHT"
            elif text_lower in ("izquierdo", "left"):
                foot = "LEFT"
            elif text_lower in ("ambidiestro", "both"):
                foot = "BOTH"

        # Valor de mercado
        market_value: int | None = None
        value_cell = row.select_one("td.rechts.hauptlink a")
        if value_cell:
            market_value = TransfermarktClient._parse_market_value(
                value_cell.get_text(strip=True)
            )

        # Contrato hasta
        contract_until: str | None = None
        # Suele estar en la última o penúltima celda
        for cell in reversed(cells):
            text = cell.get_text(strip=True)
            if len(text) == 10 and text.count("/") == 2:
                contract_until = text
                break
            if len(text) == 10 and text.count(".") == 2:
                contract_until = text
                break

        return {
            "name": name,
            "external_id": external_id,
            "position_raw": position_raw,
            "date_of_birth": dob,
            "nationality": nationality,
            "height_cm": height_cm,
            "foot": foot,
            "jersey_number": jersey,
            "market_value": market_value,
            "contract_until": contract_until,
        }

    @staticmethod
    def _parse_market_value(text: str) -> int | None:
        """Parsea texto de valor como '€1.50m', '€500k', '€200Th.' → entero EUR."""
        if not text:
            return None
        text = text.replace("€", "").replace("$", "").strip()
        multiplier = 1
        text_lower = text.lower()
        if "bn" in text_lower or "bill" in text_lower:
            multiplier = 1_000_000_000
            text = text_lower.split("bn")[0].split("bill")[0]
        elif "m" in text_lower and "th" not in text_lower:
            multiplier = 1_000_000
            text = text_lower.split("m")[0]
        elif "k" in text_lower or "th" in text_lower:
            multiplier = 1_000
            text = text_lower.split("k")[0].split("th")[0]
        try:
            return int(float(text.strip().replace(",", ".")) * multiplier)
        except (ValueError, TypeError):
            return None
