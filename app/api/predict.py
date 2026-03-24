from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.repositories.football.match_repository import MatchRepository
from app.repositories.prediction.market_odds_repository import MarketOddsRepository
from app.repositories.prediction.model_repository import ModelRepository
from app.repositories.prediction.prediction_repository import PredictionRepository
from app.services.prediction.prediction_service import PredictionService
from app.services.prediction.value_service import ValueService, odds_to_probs, compute_edge, compute_kelly_stake, compute_stake_rating

router = APIRouter()


# ── helpers ───────────────────────────────────────────────────────────────

def _fair_odds(prob: float) -> float:
    if prob <= 0:
        return 999.0
    return min(round(1.0 / prob, 2), 999.0)


def _value_analysis(p_home: float, p_draw: float, p_away: float,
                    odds_home: float, odds_draw: float, odds_away: float) -> dict:
    """Compare model probabilities against bookmaker odds.

    Uses the centralised power-method (odds_to_probs) and multiplicative
    edge (compute_edge) from value_service so all endpoints report
    identical mathematics.
    """
    market_probs = odds_to_probs(odds_home, odds_draw, odds_away)
    model_probs = {"p_home": p_home, "p_draw": p_draw, "p_away": p_away}
    edges = compute_edge(model_probs, market_probs)

    markets = []
    for label, prob, odd, edge_key in [
        ("home", p_home, odds_home, "edge_home"),
        ("draw", p_draw, odds_draw, "edge_draw"),
        ("away", p_away, odds_away, "edge_away"),
    ]:
        p_market = market_probs.get(f"p_{label}", 0.0)
        mult_edge = edges[edge_key]  # multiplicative edge (P_model/P_market - 1)
        ks = compute_kelly_stake(prob, odd)  # centralised Kelly + risk caps
        markets.append({
            "market": label,
            "bookmaker_odds": odd,
            "model_prob": round(prob, 4),
            "implied_prob": round(p_market, 4),
            "edge": round(mult_edge, 4),
            "edge_additive": round(ks["edge"], 4),
            "ev": round(mult_edge, 4),
            "kelly_raw": round(ks["kelly_raw"], 4),
            "recommended_stake_percent": round(ks["recommended_stake_percent"], 4),
            "stake_rating": compute_stake_rating(ks["recommended_stake_percent"]),
            "is_value": mult_edge > 0,
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
    db: Session = Depends(get_db),
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

                # Kelly stake recommendation per outcome
                stakes = {}
                for label, prob, odd in [
                    ("home", pred.p_home, consensus["home_odds"]),
                    ("draw", pred.p_draw, consensus["draw_odds"]),
                    ("away", pred.p_away, consensus["away_odds"]),
                ]:
                    ks = compute_kelly_stake(prob, odd)
                    stakes[label] = {
                        "recommended_stake_percent": round(ks["recommended_stake_percent"], 4),
                        "kelly_raw": round(ks["kelly_raw"], 4),
                        "edge_additive": round(ks["edge"], 4),
                        "stake_rating": compute_stake_rating(ks["recommended_stake_percent"]),
                    }
                item["recommended_stakes"] = stakes

        items.append(item)

    return {"count": len(items), "matches": items}


@router.get("/{match_id}")
def predict(
    match_id: int,
    force: bool = Query(False, description="Ignorar caché y recalcular predicción"),
    odds_home: float | None = Query(None, description="Cuota decimal del local"),
    odds_draw: float | None = Query(None, description="Cuota decimal del empate"),
    odds_away: float | None = Query(None, description="Cuota decimal del visitante"),
    db: Session = Depends(get_db),
):
    """
    Predicción Dixon-Coles para un partido.

    Sin cuotas: devuelve probabilidades, xG, mercados O/U, BTTS, doble chance.
    Con cuotas (odds_home, odds_draw, odds_away): incluye análisis de valor
    con EV, Kelly criterion y detección de value bets.
    """
    service = PredictionService(db)
    result = service.predict_match(match_id, force=force)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No se pudo generar predicción. Verifica el ID y que haya datos suficientes.",
        )

    # Serialize to dict for JSON response, then augment with value analysis
    response = result.to_dict()

    # Add fair odds to response
    response["fair_odds"] = {
        "home": _fair_odds(result.p_home),
        "draw": _fair_odds(result.p_draw),
        "away": _fair_odds(result.p_away),
    }

    # If bookmaker odds provided, add value analysis
    if odds_home is not None and odds_draw is not None and odds_away is not None:
        response["value_analysis"] = _value_analysis(
            result.p_home, result.p_draw, result.p_away,
            odds_home, odds_draw, odds_away,
        )

    # Always attach stored market data if available
    value_svc = ValueService(db)
    stored_value = value_svc.match_value(match_id)
    if stored_value:
        response["market_odds"] = stored_value["market_odds"]
        response["market_probabilities"] = stored_value["market_probabilities"]
        response["edge"] = stored_value["edge"]

        # Kelly stake recommendation per outcome
        mo = stored_value["market_odds"]
        stakes = {}
        for label, prob, odd in [
            ("home", result.p_home, mo["home"]),
            ("draw", result.p_draw, mo["draw"]),
            ("away", result.p_away, mo["away"]),
        ]:
            ks = compute_kelly_stake(prob, odd)
            stakes[label] = {
                "recommended_stake_percent": round(ks["recommended_stake_percent"], 4),
                "kelly_raw": round(ks["kelly_raw"], 4),
                "edge_additive": round(ks["edge"], 4),
                "stake_rating": compute_stake_rating(ks["recommended_stake_percent"]),
            }
        response["recommended_stakes"] = stakes
    else:
        response.setdefault("market_odds", None)
        response.setdefault("market_probabilities", None)
        response.setdefault("edge", None)
        response.setdefault("recommended_stakes", None)

    return response


@router.get("/value-bets/top")
def value_bets(
    min_edge: float = Query(0.03, ge=0.0, le=1.0, description="Edge mínimo para filtrar"),
    limit: int = Query(20, ge=1, le=100, description="Máximo de resultados"),
    db: Session = Depends(get_db),
):
    """Top value bets: partidos donde el modelo detecta mayor edge vs mercado."""
    svc = ValueService(db)
    results = svc.top_value_bets(min_edge=min_edge, limit=limit)
    return {"count": len(results), "value_bets": results}
