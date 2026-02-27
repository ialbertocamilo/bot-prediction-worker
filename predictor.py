"""Predicción de ganador basada en estadísticas (tabla, historial, forma, Poisson, Elo)."""
from scipy.stats import poisson

from football_api import FootballAPI, FootballAPIError

# Pesos para combinar criterios: H2H+tabla+forma, Poisson, Elo
WEIGHT_CLASSIC = 0.35
WEIGHT_POISSON = 0.35
WEIGHT_ELO = 0.30

# Parámetros Elo
ELO_K = 32
ELO_INITIAL = 1500

# Máximo de goles a considerar en Poisson (por equipo)
POISSON_MAX_GOALS = 10


def _points_last_n(matches: list, team_id: int, n: int = 5) -> tuple[int, int, int]:
    """Puntos en los últimos n partidos: (victorias, empates, derrotas)."""
    wins, draws, losses = 0, 0, 0
    for m in matches[:n]:
        home_id = m.get("homeTeam", {}).get("id")
        away_id = m.get("awayTeam", {}).get("id")
        score = m.get("score", {}).get("fullTime") or {}
        h = score.get("home") is not None and score.get("away") is not None
        if not h:
            continue
        hg, ag = score.get("home", 0), score.get("away", 0)
        if home_id == team_id:
            if hg > ag:
                wins += 1
            elif hg < ag:
                losses += 1
            else:
                draws += 1
        else:
            if ag > hg:
                wins += 1
            elif ag < hg:
                losses += 1
            else:
                draws += 1
    return wins, draws, losses


def _standings_points(api: FootballAPI, competition_code: str, team_id: int) -> int | None:
    """Puntos del equipo en la tabla de la competición, si está."""
    try:
        data = api.get_standings(competition_code)
    except FootballAPIError:
        return None
    for standing in data.get("standings", []):
        for row in standing.get("table", []):
            if row.get("team", {}).get("id") == team_id:
                return row.get("points")
    return None


def _goals_from_matches(matches: list, team_id: int, n: int = 10) -> tuple[float, float]:
    """Devuelve (media_goles_anotados, media_goles_encajados) en los últimos n partidos."""
    scored, conceded = [], []
    for m in matches[:n]:
        score = m.get("score", {}).get("fullTime") or {}
        hg = score.get("home")
        ag = score.get("away")
        if hg is None or ag is None:
            continue
        home_id = m.get("homeTeam", {}).get("id")
        if home_id == team_id:
            scored.append(hg)
            conceded.append(ag)
        else:
            scored.append(ag)
            conceded.append(hg)
    if not scored:
        return 1.0, 1.0
    return sum(scored) / len(scored), sum(conceded) / len(conceded)


def _poisson_1x2(lambda_home: float, lambda_away: float) -> tuple[float, float, float]:
    """
    Probabilidades 1X2 asumiendo goles independientes con Poisson.
    Devuelve (P(victoria local), P(empate), P(victoria visitante)).
    """
    if lambda_home <= 0 or lambda_away <= 0:
        return 1.0 / 3, 1.0 / 3, 1.0 / 3
    p_home_win = 0.0
    p_draw = 0.0
    p_away_win = 0.0
    for i in range(POISSON_MAX_GOALS + 1):
        for j in range(POISSON_MAX_GOALS + 1):
            p = poisson.pmf(i, lambda_home) * poisson.pmf(j, lambda_away)
            if i > j:
                p_home_win += p
            elif i < j:
                p_away_win += p
            else:
                p_draw += p
    s = p_home_win + p_draw + p_away_win
    if s <= 0:
        return 1.0 / 3, 1.0 / 3, 1.0 / 3
    return p_home_win / s, p_draw / s, p_away_win / s


def _elo_expected(home_elo: float, away_elo: float) -> float:
    """Expectativa de victoria local (0–1) según diferencia Elo."""
    return 1.0 / (1.0 + 10.0 ** ((away_elo - home_elo) / 400.0))


def _elo_from_match_sequences(
    home_id: int,
    away_id: int,
    home_matches: list,
    away_matches: list,
) -> tuple[float, float]:
    """
    Calcula Elo actual de local y visitante procesando partidos en orden cronológico.
    Usa ELO_INITIAL y ELO_K.
    """
    # Unir partidos y ordenar por fecha (más antiguos primero)
    all_matches = []
    for m in home_matches + away_matches:
        utc = m.get("utcDate") or m.get("date") or ""
        score = m.get("score", {}).get("fullTime") or {}
        if score.get("home") is not None and score.get("away") is not None and utc:
            all_matches.append(m)
    all_matches.sort(key=lambda x: x.get("utcDate", "") or x.get("date", ""))

    elo: dict[int, float] = {}
    for m in all_matches:
        hid = m.get("homeTeam", {}).get("id")
        aid = m.get("awayTeam", {}).get("id")
        if hid is None or aid is None:
            continue
        if hid not in elo:
            elo[hid] = ELO_INITIAL
        if aid not in elo:
            elo[aid] = ELO_INITIAL
        hg = m.get("score", {}).get("fullTime", {}).get("home", 0)
        ag = m.get("score", {}).get("fullTime", {}).get("away", 0)
        if hg > ag:
            actual_home = 1.0
        elif hg < ag:
            actual_home = 0.0
        else:
            actual_home = 0.5
        e_home = _elo_expected(elo[hid], elo[aid])
        diff = ELO_K * (actual_home - e_home)
        elo[hid] = elo[hid] + diff
        elo[aid] = elo[aid] - diff

    home_elo = elo.get(home_id, ELO_INITIAL)
    away_elo = elo.get(away_id, ELO_INITIAL)
    return home_elo, away_elo


def _elo_to_1x2(home_elo: float, away_elo: float, draw_share: float = 0.25) -> tuple[float, float, float]:
    """
    Convierte Elo en probabilidades 1X2.
    draw_share: fracción aproximada que se asigna al empate (resto se reparte por expectativa).
    """
    e_home = _elo_expected(home_elo, away_elo)
    p_home = e_home * (1.0 - draw_share)
    p_away = (1.0 - e_home) * (1.0 - draw_share)
    p_draw = draw_share
    s = p_home + p_draw + p_away
    return p_home / s, p_draw / s, p_away / s


def predict_match(api: FootballAPI, match_id: int) -> dict:
    """
    Predice el resultado (ganador o empate) combinando:
    - Criterios clásicos: H2H, tabla de posiciones, forma reciente (últimos 5)
    - Poisson: medias de goles anotados/encajados → probabilidades 1X2
    - Elo: ratings calculados con últimos partidos en orden cronológico

    Los tres bloques se promedian con pesos (WEIGHT_*). Devuelve dict con
    predicted_winner, confidence, home_win_pct, draw_pct, away_win_pct, reasons, summary.
    """
    try:
        match = api.get_match(match_id)
    except FootballAPIError as e:
        return {"error": str(e)}

    home_team = match.get("homeTeam", {})
    away_team = match.get("awayTeam", {})
    home_id = home_team.get("id")
    away_id = away_team.get("id")
    comp = match.get("competition", {})
    comp_code = comp.get("code")
    comp_name = comp.get("name", "Competition")

    home_name = home_team.get("name", "Local")
    away_name = away_team.get("name", "Visitante")

    # Puntuación por criterios (más alto = más favorito)
    home_score = 0.0
    away_score = 0.0

    reasons = []

    # 1) Head to head
    try:
        h2h = api.get_head2head(match_id, limit=10)
        agg = h2h.get("aggregates", {}) or {}
        h2h_matches = h2h.get("matches", []) or []
        if h2h_matches:
            home_wins = agg.get("homeTeam", {}).get("wins") if agg else None
            away_wins = agg.get("awayTeam", {}).get("wins") if agg else None
            if home_wins is None or away_wins is None:
                home_wins, away_wins, draws = 0, 0, 0
                for m in h2h_matches:
                    score = m.get("score", {}).get("fullTime") or {}
                    hg, ag = score.get("home"), score.get("away")
                    if hg is None or ag is None:
                        continue
                    m_home_id = m.get("homeTeam", {}).get("id")
                    if hg == ag:
                        draws += 1
                    elif m_home_id == home_id:
                        if hg > ag:
                            home_wins += 1
                        else:
                            away_wins += 1
                    else:
                        if ag > hg:
                            home_wins += 1
                        else:
                            away_wins += 1
            else:
                draws = len(h2h_matches) - home_wins - away_wins
            if home_wins + away_wins + draws > 0:
                total = home_wins + away_wins + draws
                home_score += home_wins * 3.0 + draws * 1.0
                away_score += away_wins * 3.0 + draws * 1.0
                reasons.append(
                    f"H2H: {home_name} {home_wins}V-{draws}E-{away_wins}D vs {away_name}"
                )
    except FootballAPIError:
        pass

    # 2) Tabla de posiciones (si es liga)
    if comp_code:
        home_pts = _standings_points(api, comp_code, home_id)
        away_pts = _standings_points(api, comp_code, away_id)
        if home_pts is not None and away_pts is not None:
            diff = home_pts - away_pts
            if diff != 0:
                home_score += max(0, diff * 0.5)
                away_score += max(0, -diff * 0.5)
                reasons.append(f"Tabla: {home_name} {home_pts} pts vs {away_name} {away_pts} pts")

    # 3) Forma reciente (últimos 5) y datos para Poisson/Elo (últimos 20)
    hm_list: list = []
    am_list: list = []
    try:
        home_matches = api.get_team_matches(home_id, limit=20)
        away_matches = api.get_team_matches(away_id, limit=20)
        hm_list = home_matches.get("matches", []) or []
        am_list = away_matches.get("matches", []) or []
        h_w, h_d, h_l = _points_last_n(hm_list, home_id, 5)
        a_w, a_d, a_l = _points_last_n(am_list, away_id, 5)
        home_form_pts = h_w * 3 + h_d
        away_form_pts = a_w * 3 + a_d
        home_score += home_form_pts * 0.8
        away_score += away_form_pts * 0.8
        reasons.append(
            f"Forma: {home_name} {h_w}V{h_d}E{h_l}D — {away_name} {a_w}V{a_d}E{a_l}D"
        )
    except FootballAPIError:
        pass

    # 4) Poisson (medias de goles, lambda por equipo)
    poisson_home_pct = poisson_draw_pct = poisson_away_pct = 1.0 / 3
    try:
        home_avg_scored, home_avg_conceded = _goals_from_matches(hm_list, home_id, 10)
        away_avg_scored, away_avg_conceded = _goals_from_matches(am_list, away_id, 10)
        total_goals = 0
        total_matches = 0
        for m in hm_list[:10] + am_list[:10]:
            s = m.get("score", {}).get("fullTime") or {}
            if s.get("home") is not None and s.get("away") is not None:
                total_goals += s.get("home", 0) + s.get("away", 0)
                total_matches += 1
        league_avg_per_team = (total_goals / (2 * total_matches)) if total_matches else 1.0
        if league_avg_per_team <= 0:
            league_avg_per_team = 1.0
        lambda_home = (home_avg_scored * away_avg_conceded) / league_avg_per_team
        lambda_away = (away_avg_scored * home_avg_conceded) / league_avg_per_team
        poisson_home_pct, poisson_draw_pct, poisson_away_pct = _poisson_1x2(lambda_home, lambda_away)
        reasons.append(
            f"Poisson: λ local {lambda_home:.2f}, λ visitante {lambda_away:.2f} → "
            f"1:{100*poisson_home_pct:.0f}% X:{100*poisson_draw_pct:.0f}% 2:{100*poisson_away_pct:.0f}%"
        )
    except Exception:
        pass

    # 5) Elo (ratings a partir de últimos partidos en orden cronológico)
    elo_home_pct = elo_draw_pct = elo_away_pct = 1.0 / 3
    try:
        home_elo, away_elo = _elo_from_match_sequences(home_id, away_id, hm_list, am_list)
        elo_home_pct, elo_draw_pct, elo_away_pct = _elo_to_1x2(home_elo, away_elo)
        reasons.append(
            f"Elo: {home_name} {int(home_elo)} — {away_name} {int(away_elo)} → "
            f"1:{100*elo_home_pct:.0f}% X:{100*elo_draw_pct:.0f}% 2:{100*elo_away_pct:.0f}%"
        )
    except Exception:
        pass

    # Combinar criterios clásicos (H2H + tabla + forma) en porcentajes 1X2
    total = home_score + away_score
    if total <= 0:
        classic_home_pct = classic_draw_pct = classic_away_pct = 1.0 / 3
    else:
        ch = 100 * home_score / total
        ca = 100 * away_score / total
        cd = max(0, min(40, 100 - ch - ca))
        s = ch + cd + ca
        classic_home_pct = ch / s
        classic_draw_pct = cd / s
        classic_away_pct = ca / s

    # Promedio ponderado: clásico + Poisson + Elo
    home_win_pct = (
        WEIGHT_CLASSIC * classic_home_pct
        + WEIGHT_POISSON * poisson_home_pct
        + WEIGHT_ELO * elo_home_pct
    )
    draw_pct = (
        WEIGHT_CLASSIC * classic_draw_pct
        + WEIGHT_POISSON * poisson_draw_pct
        + WEIGHT_ELO * elo_draw_pct
    )
    away_win_pct = (
        WEIGHT_CLASSIC * classic_away_pct
        + WEIGHT_POISSON * poisson_away_pct
        + WEIGHT_ELO * elo_away_pct
    )
    s = home_win_pct + draw_pct + away_win_pct
    if s > 0:
        home_win_pct = round(100 * home_win_pct / s, 1)
        away_win_pct = round(100 * away_win_pct / s, 1)
        draw_pct = round(100 - home_win_pct - away_win_pct, 1)
    else:
        home_win_pct = draw_pct = away_win_pct = 33.3

    # Decidir ganador y confianza
    if total <= 0 and abs(home_win_pct - 33.3) < 10:
        predicted_winner = None
        confidence = 30
        summary = "No hay datos suficientes; predicción hacia empate por estadística."
    elif home_win_pct > away_win_pct and home_win_pct > draw_pct:
        predicted_winner = home_id
        confidence = min(85, 40 + int(home_win_pct - max(away_win_pct, draw_pct)))
        summary = f"Predicción: victoria local ({home_name})."
    elif away_win_pct > home_win_pct and away_win_pct > draw_pct:
        predicted_winner = away_id
        confidence = min(85, 40 + int(away_win_pct - max(home_win_pct, draw_pct)))
        summary = f"Predicción: victoria visitante ({away_name})."
    else:
        predicted_winner = None
        confidence = min(70, 35 + int(draw_pct))
        summary = "Predicción: empate según estadísticas."

    winner_name = None
    if predicted_winner == home_id:
        winner_name = home_name
    elif predicted_winner == away_id:
        winner_name = away_name

    return {
        "match_id": match_id,
        "home_team": home_name,
        "away_team": away_name,
        "competition": comp_name,
        "predicted_winner": predicted_winner,
        "predicted_winner_name": winner_name,
        "confidence": confidence,
        "home_win_pct": home_win_pct,
        "draw_pct": draw_pct,
        "away_win_pct": away_win_pct,
        "reasons": reasons,
        "summary": summary,
    }
