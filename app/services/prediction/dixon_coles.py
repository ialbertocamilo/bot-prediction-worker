"""
Dixon-Coles model for football match prediction.

Agnostic implementation — no database or API dependencies.
Takes raw match data and produces attack/defense ratings + match probabilities.

Reference:
    Dixon, M.J. & Coles, S.G. (1997)
    "Modelling Association Football Scores and
     Inefficiencies in the Football Betting Market"
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson as poisson_dist

logger = logging.getLogger(__name__)

MAX_GOALS = 10

@dataclass(frozen=True)
class MatchData:
    home_team_id: int
    away_team_id: int
    home_goals: int
    away_goals: int
    weight: float = 1.0


@dataclass
class DixonColesParams:
    attack: dict[int, float] = field(default_factory=dict)
    defense: dict[int, float] = field(default_factory=dict)
    home_advantage: float = 0.0
    rho: float = 0.0
    teams: list[int] = field(default_factory=list)
    converged: bool = True

def _tau(x: int, y: int, lam1: float, lam2: float, rho: float) -> float:
    """Dixon-Coles correction factor for low-scoring outcomes."""
    if x == 0 and y == 0:
        return 1.0 - lam1 * lam2 * rho
    if x == 0 and y == 1:
        return 1.0 + lam1 * rho
    if x == 1 and y == 0:
        return 1.0 + lam2 * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def _neg_log_likelihood(
    params: np.ndarray,
    home_idx: np.ndarray,
    away_idx: np.ndarray,
    hg: np.ndarray,
    ag: np.ndarray,
    weights: np.ndarray,
    n_teams: int,
    xg_att_prior: np.ndarray,
    xg_def_prior: np.ndarray,
    xg_mask: np.ndarray,
    xg_weight: float,
) -> float:
    attack = params[:n_teams]
    defense = params[n_teams: 2 * n_teams]
    home_adv = params[2 * n_teams]
    rho = params[2 * n_teams + 1]

    lam1 = np.exp(attack[home_idx] + defense[away_idx] + home_adv)
    lam2 = np.exp(attack[away_idx] + defense[home_idx])

    ll = hg * np.log(lam1) - lam1 + ag * np.log(lam2) - lam2

    tau_vals = np.ones(len(hg))
    m00 = (hg == 0) & (ag == 0)
    m10 = (hg == 1) & (ag == 0)
    m01 = (hg == 0) & (ag == 1)
    m11 = (hg == 1) & (ag == 1)

    tau_vals[m00] = 1.0 - lam1[m00] * lam2[m00] * rho
    tau_vals[m10] = 1.0 + lam2[m10] * rho
    tau_vals[m01] = 1.0 + lam1[m01] * rho
    tau_vals[m11] = 1.0 - rho

    tau_vals = np.maximum(tau_vals, 1e-10)
    ll += np.log(tau_vals)

    ll *= weights

    penalty = 100.0 * np.sum(attack) ** 2

    # xG-based regularization: pull attack/defense toward xG-implied priors
    penalty_xg = 0.0
    if xg_weight > 0.0:
        penalty_xg = xg_weight * np.sum(
            xg_mask * ((attack - xg_att_prior) ** 2
                       + (defense - xg_def_prior) ** 2)
        )

    return -np.sum(ll) + penalty + penalty_xg

class DixonColesModel:
    def __init__(self, time_decay: float = 0.005, home_adv_init: float = 0.25) -> None:
        self.time_decay = time_decay
        self.home_adv_init = home_adv_init
        self.params: DixonColesParams | None = None

    def fit(
        self,
        matches: list[MatchData],
        xg_priors: dict[int, tuple[float, float]] | None = None,
        xg_weight: float = 0.0,
    ) -> DixonColesParams:
        if len(matches) < 10:
            raise ValueError("Se necesitan al menos 10 partidos para ajustar Dixon-Coles")

        teams = sorted({m.home_team_id for m in matches} | {m.away_team_id for m in matches})
        idx = {t: i for i, t in enumerate(teams)}
        n = len(teams)

        hi = np.array([idx[m.home_team_id] for m in matches])
        ai = np.array([idx[m.away_team_id] for m in matches])
        hg = np.array([m.home_goals for m in matches], dtype=np.float64)
        ag = np.array([m.away_goals for m in matches], dtype=np.float64)
        w = np.array([m.weight for m in matches], dtype=np.float64)

        # Build xG prior arrays (centered log-scale)
        xg_att_prior = np.zeros(n)
        xg_def_prior = np.zeros(n)
        xg_mask = np.zeros(n)
        eff_xg_weight = 0.0

        if xg_priors and xg_weight > 0:
            log_atts: list[float] = []
            log_defs: list[float] = []
            for i, t in enumerate(teams):
                if t in xg_priors:
                    xg_for, xg_against = xg_priors[t]
                    xg_att_prior[i] = math.log(max(xg_for, 0.1))
                    xg_def_prior[i] = math.log(max(xg_against, 0.1))
                    xg_mask[i] = 1.0
                    log_atts.append(xg_att_prior[i])
                    log_defs.append(xg_def_prior[i])
            # Center priors (compatible with sum-to-zero constraint)
            if log_atts:
                mean_att = sum(log_atts) / len(log_atts)
                mean_def = sum(log_defs) / len(log_defs)
                for i in range(n):
                    if xg_mask[i]:
                        xg_att_prior[i] -= mean_att
                        xg_def_prior[i] -= mean_def
                eff_xg_weight = xg_weight
                logger.info(
                    "xG priors: %d/%d equipos con datos xG, peso=%.1f",
                    int(np.sum(xg_mask)), n, eff_xg_weight,
                )

        x0 = np.zeros(2 * n + 2)
        x0[2 * n] = self.home_adv_init
        x0[2 * n + 1] = -0.05 

        bounds = [(None, None)] * (2 * n)
        bounds.append((None, None)) 
        bounds.append((-0.99, 0.99))  

        res = minimize(
            _neg_log_likelihood,
            x0,
            args=(hi, ai, hg, ag, w, n,
                  xg_att_prior, xg_def_prior, xg_mask, eff_xg_weight),
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 500, "ftol": 1e-8},
        )

        if not res.success:
            logger.warning("Dixon-Coles no convergió: %s", res.message)

        self.params = DixonColesParams(
            attack={teams[i]: float(res.x[i]) for i in range(n)},
            defense={teams[i]: float(res.x[n + i]) for i in range(n)},
            home_advantage=float(res.x[2 * n]),
            rho=float(res.x[2 * n + 1]),
            teams=teams,
            converged=bool(res.success),
        )
        return self.params


    def predict_match(
        self,
        home_team_id: int,
        away_team_id: int,
        params: DixonColesParams | None = None,
    ) -> dict:
        p = params or self.params
        if p is None:
            raise RuntimeError("Modelo no ajustado. Llama a fit() primero.")

        avg_att = sum(p.attack.values()) / max(len(p.attack), 1)
        avg_def = sum(p.defense.values()) / max(len(p.defense), 1)

        a_h = p.attack.get(home_team_id, avg_att)
        d_h = p.defense.get(home_team_id, avg_def)
        a_a = p.attack.get(away_team_id, avg_att)
        d_a = p.defense.get(away_team_id, avg_def)

        lam_h = math.exp(a_h + d_a + p.home_advantage)
        lam_a = math.exp(a_a + d_h)

        pm = np.zeros((MAX_GOALS + 1, MAX_GOALS + 1))
        for i in range(MAX_GOALS + 1):
            for j in range(MAX_GOALS + 1):
                pm[i, j] = max(
                    poisson_dist.pmf(i, lam_h)
                    * poisson_dist.pmf(j, lam_a)
                    * _tau(i, j, lam_h, lam_a, p.rho),
                    0.0,
                )

        total = pm.sum()
        if total > 0:
            pm /= total

        p_home = float(np.tril(pm, -1).sum())
        p_away = float(np.triu(pm, 1).sum())
        p_draw = float(np.trace(pm))
        s = p_home + p_draw + p_away
        if s > 0:
            p_home /= s
            p_draw /= s
            p_away /= s
        else:
            p_home = p_draw = p_away = 1.0 / 3

        # Over/Under totals for multiple thresholds
        p_under_1_5 = float(sum(pm[i, j] for i in range(MAX_GOALS + 1) for j in range(MAX_GOALS + 1) if i + j <= 1))
        p_under_2_5 = float(sum(pm[i, j] for i in range(MAX_GOALS + 1) for j in range(MAX_GOALS + 1) if i + j <= 2))
        p_under_3_5 = float(sum(pm[i, j] for i in range(MAX_GOALS + 1) for j in range(MAX_GOALS + 1) if i + j <= 3))

        p_btts_no = float(pm[0, :].sum()) + float(pm[:, 0].sum()) - float(pm[0, 0])
        p_btts_yes = 1.0 - p_btts_no

        # Double chance markets
        p_1x = p_home + p_draw
        p_x2 = p_draw + p_away
        p_12 = p_home + p_away

        scorelines: list[tuple[int, int, float]] = []
        for i in range(min(7, MAX_GOALS + 1)):
            for j in range(min(7, MAX_GOALS + 1)):
                scorelines.append((i, j, float(pm[i, j])))
        scorelines.sort(key=lambda x: x[2], reverse=True)
        top = {f"{s[0]}-{s[1]}": round(s[2] * 100, 1) for s in scorelines[:8]}

        return {
            "p_home": round(p_home, 4),
            "p_draw": round(p_draw, 4),
            "p_away": round(p_away, 4),
            "xg_home": round(lam_h, 2),
            "xg_away": round(lam_a, 2),
            "p_over_1_5": round(1.0 - p_under_1_5, 4),
            "p_under_1_5": round(p_under_1_5, 4),
            "p_over_2_5": round(1.0 - p_under_2_5, 4),
            "p_under_2_5": round(p_under_2_5, 4),
            "p_over_3_5": round(1.0 - p_under_3_5, 4),
            "p_under_3_5": round(p_under_3_5, 4),
            "p_btts_yes": round(p_btts_yes, 4),
            "p_btts_no": round(p_btts_no, 4),
            "p_1x": round(p_1x, 4),
            "p_x2": round(p_x2, 4),
            "p_12": round(p_12, 4),
            "top_scorelines": top,
            "rho": round(p.rho, 4),
            "lambda_home": round(lam_h, 4),
            "lambda_away": round(lam_a, 4),
            "attack_home": round(a_h, 4),
            "defense_home": round(d_h, 4),
            "attack_away": round(a_a, 4),
            "defense_away": round(d_a, 4),
        }


