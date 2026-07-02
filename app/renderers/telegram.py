from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.db.models.football.match import Match
from app.repositories.football.match_repository import MatchRepository
from app.services.prediction.value_service import compute_kelly_stake, compute_stake_rating

if TYPE_CHECKING:
    from app.services.canonical_league_service import CanonicalLeagueService


def _esc(text: str) -> str:
    """Escape HTML special characters for Telegram."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _confidence_label(prob: float) -> str:
    if prob >= 0.70:
        return "🟢 Alta"
    if prob >= 0.50:
        return "🟡 Media"
    if prob >= 0.35:
        return "🟠 Baja"
    return "🔴 Muy baja"


def _pct(v: float) -> str:
    """Format a probability as percentage string."""
    return f"{v * 100:.1f}%"


def _render_matches(
    upcoming: list[Match],
    svc: CanonicalLeagueService,
    filter_label: str = "",
) -> str:
    """Build HTML text for a list of matches."""
    lines = [f"⚽ <b>Partidos{_esc(filter_label)}</b>\n"]
    current_league = ""

    for idx, m in enumerate(upcoming[:30], 1):
        league_name = svc.display_name_for(m.league_id)
        if league_name != current_league:
            current_league = league_name
            lines.append(f"\n🏆 <b>{_esc(league_name)}</b>")

        home = _esc(m.home_team.name if m.home_team else "?")
        away = _esc(m.away_team.name if m.away_team else "?")
        rnd = f" (J{_esc(m.round)})" if m.round else ""

        if m.status == "IN_PLAY":
            clock = _esc(m.clock_display) if m.clock_display else "En vivo"
            hg = m.home_goals if m.home_goals is not None else 0
            ag = m.away_goals if m.away_goals is not None else 0
            lines.append(
                f"  <b>{idx}.</b> 🔴 <b>EN VIVO</b> ({clock})\n"
                f"      {home} <b>{hg}</b> - <b>{ag}</b> {away}{rnd}"
            )
        elif m.status == "FINISHED":
            hg = m.home_goals if m.home_goals is not None else "?"
            ag = m.away_goals if m.away_goals is not None else "?"
            lines.append(
                f"  <b>{idx}.</b> ✅ <b>TERMINADO</b>\n"
                f"      {home} <b>{hg}</b> - <b>{ag}</b> {away}{rnd}"
            )
        else:
            date_str = m.utc_date.strftime("%d/%m %H:%M") if m.utc_date else "?"
            lines.append(
                f"  <b>{idx}.</b> ⏳ {date_str} UTC\n"
                f"      {home} vs {away}{rnd}"
            )

    lines.append(
        "\n<i>🔮 Toca <b>Predecir</b> o usa /predict &lt;número&gt;</i>"
    )
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3950] + "\n..."
    return text


def _format_prediction(result) -> str:
    """Build a clean, user-focused prediction message."""
    home = _esc(result.home_team)
    away = _esc(result.away_team)
    p_h = result.p_home
    p_d = result.p_draw
    p_a = result.p_away

    if p_h >= p_d and p_h >= p_a:
        tip, conf = f"1 ({home})", p_h
    elif p_a >= p_d:
        tip, conf = f"2 ({away})", p_a
    else:
        tip, conf = "X (Empate)", p_d

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━",
        f"⚽ <b>{home}  vs  {away}</b>",
        f"🏆 {_esc(result.league or '')}",
    ]
    if result.utc_date:
        lines.append(f"🕐 {result.utc_date.strftime('%d/%m/%Y %H:%M')} UTC")

    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    lines.append(
        f"\n🔮 <b>Predicción: {tip}</b>\n"
        f"    {_confidence_label(conf)}  ({_pct(conf)})"
    )

    bar_h = round(p_h * 20)
    bar_d = round(p_d * 20)
    bar_a = 20 - bar_h - bar_d
    bar_a = max(0, bar_a)
    lines.append(
        f"\n📊 <b>Probabilidades 1X2</b>\n"
        f"    🏠 {home}  <b>{_pct(p_h)}</b>  {'▓' * bar_h}{'░' * (20 - bar_h)}\n"
        f"    🤝 Empate  <b>{_pct(p_d)}</b>  {'▓' * bar_d}{'░' * (20 - bar_d)}\n"
        f"    ✈️ {away}  <b>{_pct(p_a)}</b>  {'▓' * bar_a}{'░' * (20 - bar_a)}"
    )

    ou_parts: list[str] = []
    if result.p_over_1_5 is not None:
        ou_parts.append(f"O1.5 <b>{_pct(result.p_over_1_5)}</b>")
    if result.p_over_2_5 is not None:
        ou_parts.append(f"O2.5 <b>{_pct(result.p_over_2_5)}</b>")
    if result.p_over_3_5 is not None:
        ou_parts.append(f"O3.5 <b>{_pct(result.p_over_3_5)}</b>")
    if ou_parts:
        lines.append(f"\n⬆️ <b>Over/Under</b>\n    {' · '.join(ou_parts)}")

    if result.p_btts_yes is not None:
        lines.append(
            f"\n🎯 <b>Ambos Anotan (BTTS)</b>\n"
            f"    Sí <b>{_pct(result.p_btts_yes)}</b>  ·  "
            f"No <b>{_pct(result.p_btts_no)}</b>"
        )

    lines.append(
        f"\n🔄 <b>Doble Oportunidad</b>\n"
        f"    1X — {home} o Empate: <b>{_pct(result.p_1x)}</b>\n"
        f"    X2 — {away} o Empate: <b>{_pct(result.p_x2)}</b>\n"
        f"    12 — {home} o {away}: <b>{_pct(result.p_12)}</b>"
    )

    top = result.top_scorelines
    if top:
        scores = [f"<b>{s}</b> ({p}%)" for s, p in list(top.items())[:3]]
        lines.append(f"\n🥅 <b>Marcadores más probables</b>\n    {', '.join(scores)}")

    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    lines.append("<i>💡 Toca el botón de abajo para comparar con tus cuotas.</i>")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3950] + "\n..."
    return text


def _format_stake_analysis(
    result,
    home_odds: float,
    draw_odds: float,
    away_odds: float,
) -> str:
    """Build the stake analysis message after user provides odds."""
    from app.services.prediction.value_service import odds_to_probs

    home = _esc(result.home_team)
    away = _esc(result.away_team)
    market = odds_to_probs(home_odds, draw_odds, away_odds)

    outcomes = [
        ("1", home, result.p_home, home_odds, market["p_home"]),
        ("X", "Empate", result.p_draw, draw_odds, market["p_draw"]),
        ("2", away, result.p_away, away_odds, market["p_away"]),
    ]

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━",
        f"💰 <b>Análisis de Cuotas</b>",
        f"⚽ {home} vs {away}",
        f"📊 Cuotas: <b>{home_odds:.2f}</b> / <b>{draw_odds:.2f}</b> / <b>{away_odds:.2f}</b>",
        f"📉 Margen casa: <b>{market['margin'] * 100:.1f}%</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
    ]

    best_edge = -999.0
    best_label = ""

    for code, label, model_p, odds, market_p in outcomes:
        ks = compute_kelly_stake(model_p, odds)
        edge = ks["edge"]
        stake_pct = ks["recommended_stake_percent"]
        rating = compute_stake_rating(stake_pct)
        stake_bar = "🟢" * rating + "⚪" * (10 - rating)

        edge_sign = "+" if edge > 0 else ""
        value_tag = " ✅ VALOR" if edge > 0.03 else ""

        lines.append(
            f"\n<b>{code} — {label}</b>  @{odds:.2f}\n"
            f"    Modelo: <b>{_pct(model_p)}</b> vs Casa: <b>{_pct(market_p)}</b>\n"
            f"    📈 Edge: <b>{edge_sign}{edge * 100:.1f}%</b>{value_tag}\n"
            f"    🎯 Stake: {stake_bar} <b>{rating}/10</b>"
        )
        if stake_pct > 0:
            lines.append(f"    💵 Apostar: <b>{stake_pct * 100:.2f}%</b> del bankroll")

        if edge > best_edge:
            best_edge = edge
            best_label = f"{code} ({label})"

    lines.append("\n━━━━━━━━━━━━━━━━━━━━━")
    if best_edge > 0.03:
        lines.append(
            f"\n🏆 <b>Mejor apuesta: {best_label}</b>\n"
            f"    Edge: <b>+{best_edge * 100:.1f}%</b> sobre la casa"
        )
    elif best_edge > 0:
        lines.append(
            f"\n⚠️ <b>Edge pequeño en {best_label}</b> ({best_edge * 100:.1f}%)\n"
            f"    Considerar con precaución."
        )
    else:
        lines.append(
            "\n❌ <b>Sin valor detectado</b>\n"
            "    Las cuotas no ofrecen ventaja. Mejor pasar."
        )

    lines.append(
        "\n━━━━━━━━━━━━━━━━━━━━━"
        "\n📖 <b>¿Qué significa cada dato?</b>\n"
        "  📈 <b>Edge</b> — Ventaja del modelo sobre la casa de apuestas. "
        "Si es positivo, la cuota paga más de lo que debería.\n"
        "  🎯 <b>Stake</b> — Cuánto apostar según el criterio de Kelly. "
        "Más 🟢 = más confianza en la apuesta.\n"
        "  📉 <b>Margen casa</b> — Comisión implícita de la casa. "
        "Cuanto menor, mejores cuotas te ofrecen.\n"
        "  ✅ <b>VALOR</b> — Aparece cuando el edge supera 3%, "
        "indicando una apuesta con ventaja real."
    )

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3950] + "\n..."
    return text


def _format_value_bets(bets: list[dict], db: Session) -> str:
    """Build HTML message for top value bets."""
    repo = MatchRepository(db)
    lines = ["📈 <b>Top Value Bets</b>\n"]

    for i, bet in enumerate(bets, 1):
        match = repo.get_by_id(bet["match_id"])
        if not match:
            continue

        home = _esc(match.home_team.name if match.home_team else "?")
        away = _esc(match.away_team.name if match.away_team else "?")
        best = bet["best_value"]
        outcome_map = {"home": f"1 ({home})", "draw": "X", "away": f"2 ({away})"}
        outcome_label = outcome_map.get(best["outcome"], best["outcome"])
        edge_pct = best["edge"] * 100

        odds_data = bet["market_odds"]
        date_str = match.utc_date.strftime("%d/%m %H:%M") if match.utc_date else ""

        outcome_key = best["outcome"]
        model_p = bet["model_probabilities"][f"p_{outcome_key}"]
        outcome_odds = odds_data[outcome_key]
        ks = compute_kelly_stake(model_p, outcome_odds)
        rating = compute_stake_rating(ks["recommended_stake_percent"])
        stake_bar = "🟢" * rating + "⚪" * (10 - rating)

        lines.append(
            f"<b>{i}.</b> {home} vs {away}\n"
            f"    🕐 {date_str} UTC\n"
            f"    💰 Apuesta: <b>{outcome_label}</b>\n"
            f"    📈 Edge: <b>+{edge_pct:.1f}%</b>\n"
            f"    🎯 Stake: {stake_bar} <b>{rating}/10</b>\n"
            f"    📊 Cuotas: {odds_data['home']:.2f} / {odds_data['draw']:.2f} / {odds_data['away']:.2f}\n"
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>📈 Edge = ventaja del modelo sobre el mercado\n"
        "💰 Stake = % del bankroll (Kelly fraccionado 10%, tope 5%)\n"
        "🟢 = unidad de stake recomendada</i>"
    )

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3950] + "\n..."
    return text
