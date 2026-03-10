from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from app.domain.canonical import CanonicalMatchStats, CanonicalPlayer, CanonicalSourceRef
from app.domain.enums import FootPreference, PlayerPosition

logger = logging.getLogger(__name__)

SOURCE_NAME = "sofascore-scraper"


class SofaScoreMapper:
    """Convierte respuestas JSON de SofaScore → modelos canónicos."""

    # ── Mapeo de posiciones SofaScore → PlayerPosition ──────────

    _POSITION_MAP: dict[str, PlayerPosition] = {
        "G": PlayerPosition.goalkeeper,
        "D": PlayerPosition.defender,
        "M": PlayerPosition.midfielder,
        "F": PlayerPosition.forward,
    }

    # ── Mapeo de estadísticas → campos canónicos ────────────────
    _STAT_MAP: dict[str, str] = {
        "ball possession": "possession_pct",
        "possession": "possession_pct",
        "ballpossession": "possession_pct",
        "total shots": "shots",
        "totalshots": "shots",
        "shots on target": "shots_on_target",
        "shotsontarget": "shots_on_target",
        "expected goals": "xg",
        "expectedgoals": "xg",
        "xg": "xg",
        "expected goals against": "xga",
        "xga": "xga",
        "corner kicks": "corners",
        "cornerkicks": "corners",
        "corners": "corners",
        "fouls": "fouls",
        "offsides": "offsides",
        "yellow cards": "yellow_cards",
        "yellowcards": "yellow_cards",
        "red cards": "red_cards",
        "redcards": "red_cards",
        "total passes": "passes",
        "totalpasses": "passes",
        "passes": "passes",
        "accurate passes": "_accurate_passes",
        "accuratepasses": "_accurate_passes",
    }

    @staticmethod
    def map_match_stats(
        stats_payload: dict[str, Any],
        event_id: str,
        home_team_name: str,
        away_team_name: str,
    ) -> list[CanonicalMatchStats]:
        """Convierte la respuesta de /event/{id}/statistics → 2 CanonicalMatchStats.

        SofaScore retorna estadísticas agrupadas por período.  Usamos el
        período 'ALL' (todo el partido).

        La estructura típica es::

            {
              "statistics": [
                {
                  "period": "ALL",
                  "groups": [
                    {
                      "groupName": "Possession",
                      "statisticsItems": [
                        {
                          "name": "Ball possession",
                          "home": "58%",
                          "away": "42%",
                          "homeValue": 58,
                          "awayValue": 42
                        },
                        ...
                      ]
                    }
                  ]
                }
              ]
            }
        """
        statistics: list[dict[str, Any]] = stats_payload.get("statistics", [])

        # Buscar el período "ALL"
        all_period: dict[str, Any] | None = None
        for period in statistics:
            if period.get("period") == "ALL":
                all_period = period
                break

        # Fallback: usar el primer período disponible
        if all_period is None and statistics:
            all_period = statistics[0]

        if all_period is None:
            return []

        home_stats: dict[str, float | int | None] = {}
        away_stats: dict[str, float | int | None] = {}

        for group in all_period.get("groups", []):
            for item in group.get("statisticsItems", []):
                stat_name: str = item.get("name", "").lower().strip()
                canonical_field = SofaScoreMapper._STAT_MAP.get(stat_name)
                if canonical_field is None:
                    continue

                home_val = SofaScoreMapper._extract_value(
                    item.get("homeValue", item.get("home")),
                    canonical_field,
                )
                away_val = SofaScoreMapper._extract_value(
                    item.get("awayValue", item.get("away")),
                    canonical_field,
                )

                home_stats[canonical_field] = home_val
                away_stats[canonical_field] = away_val

        # Calcular pass_accuracy_pct = (accurate_passes / total_passes) * 100
        for stats_dict in (home_stats, away_stats):
            accurate = stats_dict.pop("_accurate_passes", None)
            total = stats_dict.get("passes")
            if accurate is not None and total and total > 0:
                stats_dict["pass_accuracy_pct"] = round((accurate / total) * 100, 1)

        now_str = datetime.utcnow().isoformat()
        result: list[CanonicalMatchStats] = []

        if home_stats:
            result.append(
                CanonicalMatchStats(
                    match_external_id=event_id,
                    team_name=home_team_name,
                    source_ref=CanonicalSourceRef(
                        source_name=SOURCE_NAME,
                        entity_type="match_stats",
                        external_id=f"{event_id}-home",
                    ),
                    **home_stats,
                )
            )

        if away_stats:
            result.append(
                CanonicalMatchStats(
                    match_external_id=event_id,
                    team_name=away_team_name,
                    source_ref=CanonicalSourceRef(
                        source_name=SOURCE_NAME,
                        entity_type="match_stats",
                        external_id=f"{event_id}-away",
                    ),
                    **away_stats,
                )
            )

        return result

    # ── Lineups → CanonicalPlayer ───────────────────────────────

    @staticmethod
    def map_players_from_lineups(
        lineups_payload: dict[str, Any],
        home_team_name: str,
        away_team_name: str,
    ) -> list[CanonicalPlayer]:
        """Convierte /event/{id}/lineups → lista de CanonicalPlayer.

        Extrae jugadores de ambos equipos (titulares + suplentes).
        Cada jugador se identifica por su ``player.id`` de SofaScore,
        lo que permite deduplicación cuando el mismo jugador aparece
        en múltiples partidos.
        """
        players: list[CanonicalPlayer] = []

        for side, team_name in (("home", home_team_name), ("away", away_team_name)):
            side_data: dict[str, Any] = lineups_payload.get(side, {})
            for entry in side_data.get("players", []):
                mapped = SofaScoreMapper._map_single_player(entry, team_name)
                if mapped is not None:
                    players.append(mapped)

        return players

    @staticmethod
    def _map_single_player(
        entry: dict[str, Any],
        team_name: str,
    ) -> CanonicalPlayer | None:
        """Mapea un dict de jugador del lineup a CanonicalPlayer."""
        player_data: dict[str, Any] = entry.get("player", {})
        name: str = player_data.get("name", "").strip()
        if not name:
            return None

        player_id: int | str = player_data.get("id", "")
        if not player_id:
            return None

        # Posición
        pos_raw: str = player_data.get("position", "")
        position = SofaScoreMapper._POSITION_MAP.get(pos_raw, PlayerPosition.unknown)

        # Altura (SofaScore da en cm directamente)
        height_cm: int | None = player_data.get("height")

        # Nacionalidad
        country: dict[str, Any] = player_data.get("country", {})
        nationality: str | None = country.get("name")

        # Fecha de nacimiento (Unix timestamp)
        dob: date | None = None
        dob_ts: int | None = player_data.get("dateOfBirthTimestamp")
        if dob_ts is not None:
            dob = datetime.utcfromtimestamp(dob_ts).date()

        # Valor de mercado
        market_value: int | None = None
        mv_raw: dict[str, Any] | None = player_data.get("proposedMarketValueRaw")
        if mv_raw and mv_raw.get("currency") == "EUR":
            market_value = mv_raw.get("value")

        # Dorsal: preferir shirtNumber del entry (número real usado en el partido)
        jersey: int | None = None
        shirt = entry.get("shirtNumber")
        if shirt is not None:
            try:
                jersey = int(shirt)
            except (ValueError, TypeError):
                pass

        return CanonicalPlayer(
            name=name,
            date_of_birth=dob,
            nationality=nationality,
            position=position,
            height_cm=height_cm,
            team_name=team_name,
            jersey_number=jersey,
            market_value_eur=market_value,
            source_ref=CanonicalSourceRef(
                source_name=SOURCE_NAME,
                entity_type="player",
                external_id=str(player_id),
            ),
        )

    @staticmethod
    def _extract_value(
        raw: Any,
        field_name: str,
    ) -> float | int | None:
        """Limpia y convierte un valor de estadística de SofaScore."""
        if raw is None:
            return None

        # Si ya es numérico
        if isinstance(raw, (int, float)):
            # Porcentajes: SofaScore los da como 58 (no 0.58)
            if field_name in ("possession_pct", "pass_accuracy_pct"):
                return float(raw)
            # xG viene como float
            if field_name in ("xg", "xga"):
                return float(raw)
            return int(raw) if isinstance(raw, int) else raw

        # Si es string (e.g. "58%", "1.23")
        text = str(raw).strip().replace("%", "").replace(",", ".")
        if not text or text == "-":
            return None

        # pass_accuracy_pct puede venir como "85%" o como fracción "340/400"
        if field_name == "pass_accuracy_pct" and "/" in text:
            parts = text.split("/")
            try:
                num = float(parts[0])
                den = float(parts[1])
                return round((num / den) * 100, 1) if den > 0 else None
            except (ValueError, IndexError, ZeroDivisionError):
                return None

        try:
            val = float(text)
            if field_name in ("possession_pct", "pass_accuracy_pct", "xg", "xga"):
                return val
            return int(val)
        except ValueError:
            return None
