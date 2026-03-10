from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.domain.canonical import CanonicalPlayer, CanonicalSourceRef
from app.domain.enums import FootPreference, PlayerPosition

SOURCE_NAME = "transfermarkt-scraper"


class TransfermarktMapper:
    """Convierte datos crudos de Transfermarkt → CanonicalPlayer."""

    _POSITION_MAP: dict[str, PlayerPosition] = {
        "portero": PlayerPosition.goalkeeper,
        "goalkeeper": PlayerPosition.goalkeeper,
        "keeper": PlayerPosition.goalkeeper,
        "defensa central": PlayerPosition.defender,
        "lateral izquierdo": PlayerPosition.defender,
        "lateral derecho": PlayerPosition.defender,
        "centre-back": PlayerPosition.defender,
        "left-back": PlayerPosition.defender,
        "right-back": PlayerPosition.defender,
        "defender": PlayerPosition.defender,
        "defensa": PlayerPosition.defender,
        "mediocentro": PlayerPosition.midfielder,
        "mediocampista central": PlayerPosition.midfielder,
        "mediocentro ofensivo": PlayerPosition.midfielder,
        "mediocentro defensivo": PlayerPosition.midfielder,
        "interior derecho": PlayerPosition.midfielder,
        "interior izquierdo": PlayerPosition.midfielder,
        "central midfield": PlayerPosition.midfielder,
        "attacking midfield": PlayerPosition.midfielder,
        "defensive midfield": PlayerPosition.midfielder,
        "right midfield": PlayerPosition.midfielder,
        "left midfield": PlayerPosition.midfielder,
        "midfielder": PlayerPosition.midfielder,
        "mediocampista": PlayerPosition.midfielder,
        "extremo derecho": PlayerPosition.forward,
        "extremo izquierdo": PlayerPosition.forward,
        "delantero centro": PlayerPosition.forward,
        "mediapunta": PlayerPosition.forward,
        "centre-forward": PlayerPosition.forward,
        "second striker": PlayerPosition.forward,
        "left winger": PlayerPosition.forward,
        "right winger": PlayerPosition.forward,
        "forward": PlayerPosition.forward,
        "delantero": PlayerPosition.forward,
    }

    _FOOT_MAP: dict[str, FootPreference] = {
        "right": FootPreference.right,
        "derecho": FootPreference.right,
        "left": FootPreference.left,
        "izquierdo": FootPreference.left,
        "both": FootPreference.both,
        "ambidiestro": FootPreference.both,
    }

    @staticmethod
    def map_player(
        raw: dict[str, Any],
        team_name: str,
    ) -> CanonicalPlayer | None:
        """Convierte un dict crudo de parseo HTML → CanonicalPlayer."""
        name: str | None = raw.get("name")
        external_id: str | None = raw.get("external_id")
        if not name:
            return None

        position = TransfermarktMapper._map_position(raw.get("position_raw", ""))
        foot = TransfermarktMapper._map_foot(raw.get("foot"))
        dob = TransfermarktMapper._parse_dob(raw.get("date_of_birth"))
        contract = TransfermarktMapper._parse_contract(raw.get("contract_until"))

        return CanonicalPlayer(
            name=name,
            date_of_birth=dob,
            nationality=raw.get("nationality"),
            position=position,
            height_cm=raw.get("height_cm"),
            weight_kg=None,
            foot=foot,
            team_name=team_name,
            jersey_number=raw.get("jersey_number"),
            market_value_eur=raw.get("market_value"),
            contract_until=contract,
            source_ref=CanonicalSourceRef(
                source_name=SOURCE_NAME,
                entity_type="player",
                external_id=external_id or name,
            ),
        )

    @staticmethod
    def _map_position(raw: str) -> PlayerPosition:
        if not raw:
            return PlayerPosition.unknown
        return TransfermarktMapper._POSITION_MAP.get(
            raw.strip().lower(),
            PlayerPosition.unknown,
        )

    @staticmethod
    def _map_foot(raw: str | None) -> FootPreference:
        if not raw:
            return FootPreference.unknown
        return TransfermarktMapper._FOOT_MAP.get(
            raw.strip().lower(),
            FootPreference.unknown,
        )

    @staticmethod
    def _parse_dob(raw: str | None) -> date | None:
        """Intenta parsear múltiples formatos de fecha de nacimiento."""
        if not raw:
            return None
        raw = raw.strip().rstrip(",").strip()
        for fmt in ("%b %d, %Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _parse_contract(raw: str | None) -> date | None:
        """Parsea fecha de fin de contrato."""
        if not raw:
            return None
        raw = raw.strip()
        for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%b %d, %Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return None
