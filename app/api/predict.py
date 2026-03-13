from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.repositories.football.match_repository import MatchRepository
from app.repositories.prediction.market_odds_repository import MarketOddsRepository
from app.repositories.prediction.model_repository import ModelRepository
from app.repositories.prediction.prediction_repository import PredictionRepository
from app.services.prediction.prediction_service import PredictionService
from app.services.prediction.value_service import ValueService, odds_to_probs, compute_edge

router = APIRouter()


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── helpers ───────────────────────────────────────────────────────────────

def _fair_odds(prob: float) -> float:
    if prob <= 0:
        return 99.0
    return round(1.0 / prob, 2)


def _implied_prob(odds: float) -> float:
    if odds <= 0:
        return 0.0
    return 1.0 / odds


def _kelly(prob: float, odds: float) -> float:
    b = odds - 1.0
    if b <= 0:
        return 0.0
    return max((b * prob - (1.0 - prob)) / b, 0.0)


def _ev(prob: float, odds: float) -> float:
    return prob * (odds - 1.0) - (1.0 - prob)


def _value_analysis(p_home: float, p_draw: float, p_away: float,
                    odds_home: float, odds_draw: float, odds_away: float) -> dict:
    """Compare model probabilities against bookmaker odds."""
    markets = []
    for label, prob, odd in [("home", p_home, odds_home),
                              ("draw", p_draw, odds_draw),
                              ("away", p_away, odds_away)]:
        implied = _implied_prob(odd)
        ev = _ev(prob, odd)
        kelly_f = _kelly(prob, odd)
        markets.append({
            "market": label,
            "bookmaker_odds": odd,
            "model_prob": round(prob, 4),
            "implied_prob": round(implied, 4),
            "edge": round(prob - implied, 4),
            "ev": round(ev, 4),
            "kelly_fraction": round(kelly_f, 4),
            "kelly_quarter": round(kelly_f * 0.25, 4),
            "is_value": ev > 0,
        })
    return {
        "value_bets": [m for m in markets if m["is_value"]],
        "all_markets": markets,
    }


# ── endpoints ─────────────────────────────────────────────────────────────

@router.get("/upcoming")
def upcoming(
    league_id: int | None = Query(None, description="Filtrar por liga"),
    days: int = Query(7, ge=1, le=30, description="Días hacia adelante"),
    db: Session = Depends(_get_db),
):
    """Próximos partidos con predicciones pre-calculadas."""
    now = datetime.now(timezone.utc)
    date_to = now + timedelta(days=days)

    matches = MatchRepository(db).list_by_date_range(
        date_from=now, date_to=date_to, league_id=league_id,
    )
    matches = [m for m in matches if m.status in ("SCHEDULED", "NS")]

    model_rec = ModelRepository(db).get_by_name("dixon_coles_v1")
    pred_repo = PredictionRepository(db)
    odds_repo = MarketOddsRepository(db)

    items = []
    for m in matches:
        item = {
            "match_id": m.id,
            "home_team": m.home_team.name if m.home_team else None,
            "away_team": m.away_team.name if m.away_team else None,
            "league": m.league.name if m.league else None,
            "utc_date": m.utc_date.isoformat() if m.utc_date else None,
            "status": m.status,
            "prediction": None,
            "market_odds": None,
            "market_probabilities": None,
            "edge": None,
        }
        pred = None
        if model_rec:
            pred = pred_repo.latest_for_match_and_model(m.id, model_rec.id)
            if pred:
                item["prediction"] = {
                    "p_home": pred.p_home,
                    "p_draw": pred.p_draw,
                    "p_away": pred.p_away,
                    "xg_home": pred.xg_home,
                    "xg_away": pred.xg_away,
                    "p_over_2_5": pred.p_over_2_5,
                    "p_btts_yes": pred.p_btts_yes,
                    "fair_odds": {
                        "home": _fair_odds(pred.p_home),
                        "draw": _fair_odds(pred.p_draw),
                        "away": _fair_odds(pred.p_away),
                    },
                }

        # Attach stored market odds + edge if available
        consensus = odds_repo.consensus_for_match(m.id)
        if consensus:
            item["market_odds"] = {
                "home": consensus["home_odds"],
                "draw": consensus["draw_odds"],
                "away": consensus["away_odds"],
                "bookmakers": consensus["bookmakers"],
            }
            mkt = odds_to_probs(consensus["home_odds"], consensus["draw_odds"], consensus["away_odds"])
            item["market_probabilities"] = {
                "p_home": mkt["p_home"],
                "p_draw": mkt["p_draw"],
                "p_away": mkt["p_away"],
                "margin": mkt["margin"],
            }
            if pred:
                model_p = {"p_home": pred.p_home, "p_draw": pred.p_draw, "p_away": pred.p_away}
                item["edge"] = compute_edge(model_p, mkt)

        items.append(item)

    return {"count": len(items), "matches": items}


@router.get("/{match_id}")
def predict(
    match_id: int,
    odds_home: float | None = Query(None, description="Cuota decimal del local"),
    odds_draw: float | None = Query(None, description="Cuota decimal del empate"),
    odds_away: float | None = Query(None, description="Cuota decimal del visitante"),
    db: Session = Depends(_get_db),
):
    """
    Predicción Dixon-Coles para un partido.

    Sin cuotas: devuelve probabilidades, xG, mercados O/U, BTTS, doble chance.
    Con cuotas (odds_home, odds_draw, odds_away): incluye análisis de valor
    con EV, Kelly criterion y detección de value bets.
    """
    service = PredictionService(db)
    result = service.predict_match(match_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No se pudo generar predicción. Verifica el ID y que haya datos suficientes.",
        )

    # Add fair odds to response
    result["fair_odds"] = {
        "home": _fair_odds(result["p_home"]),
        "draw": _fair_odds(result["p_draw"]),
        "away": _fair_odds(result["p_away"]),
    }

    # If bookmaker odds provided, add value analysis
    if odds_home is not None and odds_draw is not None and odds_away is not None:
        result["value_analysis"] = _value_analysis(
            result["p_home"], result["p_draw"], result["p_away"],
            odds_home, odds_draw, odds_away,
        )

    # Always attach stored market data if available
    value_svc = ValueService(db)
    stored_value = value_svc.match_value(match_id)
    if stored_value:
        result["market_odds"] = stored_value["market_odds"]
        result["market_probabilities"] = stored_value["market_probabilities"]
        result["edge"] = stored_value["edge"]
    else:
        result.setdefault("market_odds", None)
        result.setdefault("market_probabilities", None)
        result.setdefault("edge", None)

    return result


@router.get("/value-bets/top")
def value_bets(
    min_edge: float = Query(0.03, ge=0.0, le=1.0, description="Edge mínimo para filtrar"),
    limit: int = Query(20, ge=1, le=100, description="Máximo de resultados"),
    db: Session = Depends(_get_db),
):
    """Top value bets: partidos donde el modelo detecta mayor edge vs mercado."""
    svc = ValueService(db)
    results = svc.top_value_bets(min_edge=min_edge, limit=limit)
    return {"count": len(results), "value_bets": results}