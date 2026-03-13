"""Structured schema for prediction results returned by PredictionService."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class MatchPredictionResult:
    """Typed result of PredictionService.predict_match().

    All consumers (bot, API endpoints, worker) can rely on this contract
    instead of untyped dicts.  Serialisation to dict is available via
    ``to_dict()`` for backwards-compatible JSON responses.
    """

    # ── Identity ──────────────────────────────────────────────────────
    match_id: int
    home_team: str
    away_team: str
    home_team_id: int
    away_team_id: int
    league: str
    utc_date: datetime | None
    status: str

    # ── 1X2 probabilities ────────────────────────────────────────────
    p_home: float
    p_draw: float
    p_away: float

    # ── Over/Under ───────────────────────────────────────────────────
    p_over_1_5: float | None = None
    p_under_1_5: float | None = None
    p_over_2_5: float | None = None
    p_under_2_5: float | None = None
    p_over_3_5: float | None = None
    p_under_3_5: float | None = None

    # ── BTTS ─────────────────────────────────────────────────────────
    p_btts_yes: float | None = None
    p_btts_no: float | None = None

    # ── Expected goals ───────────────────────────────────────────────
    xg_home: float | None = None
    xg_away: float | None = None

    # ── Scorelines & metadata ────────────────────────────────────────
    top_scorelines: dict[str, float] | None = None
    model: str = ""
    data_quality: str = ""

    # ── Double chance (derived) ──────────────────────────────────────
    @property
    def p_1x(self) -> float:
        return round(self.p_home + self.p_draw, 4)

    @property
    def p_x2(self) -> float:
        return round(self.p_draw + self.p_away, 4)

    @property
    def p_12(self) -> float:
        return round(self.p_home + self.p_away, 4)

    @property
    def p_total(self) -> float:
        return round(self.p_home + self.p_draw + self.p_away, 4)

    # ── Serialisation ────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialisation (backwards-compatible)."""
        return {
            "match_id": self.match_id,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "home_team_id": self.home_team_id,
            "away_team_id": self.away_team_id,
            "league": self.league,
            "utc_date": self.utc_date,
            "status": self.status,
            "p_home": self.p_home,
            "p_draw": self.p_draw,
            "p_away": self.p_away,
            "p_over_1_5": self.p_over_1_5,
            "p_under_1_5": self.p_under_1_5,
            "p_over_2_5": self.p_over_2_5,
            "p_under_2_5": self.p_under_2_5,
            "p_over_3_5": self.p_over_3_5,
            "p_under_3_5": self.p_under_3_5,
            "p_btts_yes": self.p_btts_yes,
            "p_btts_no": self.p_btts_no,
            "xg_home": self.xg_home,
            "xg_away": self.xg_away,
            "top_scorelines": self.top_scorelines,
            "model": self.model,
            "data_quality": self.data_quality,
            "p_1x": self.p_1x,
            "p_x2": self.p_x2,
            "p_12": self.p_12,
            "p_total": self.p_total,
        }
