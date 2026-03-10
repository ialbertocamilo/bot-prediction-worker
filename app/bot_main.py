"""
Bot de Telegram — lee datos de la DB, predice con Dixon-Coles.

Comandos:
    /start       — Bienvenida
    /ligas       — Ligas disponibles
    /tabla <id>  — Tabla de posiciones (calculada desde partidos)
    /partidos    — Partidos recientes y próximos
    /partido <id>— Detalle de un partido
    /vivo        — Partidos en vivo
    /prediccion <id> [cuota_L cuota_E cuota_V] — Predicción + análisis de valor
    /proximos [id_liga] — Próximos partidos con predicciones
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.orm import Session
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from app.db.models.football.match import Match
from app.db.models.prediction.prediction import Prediction
from app.db.session import SessionLocal
from app.repositories.football.league_repository import LeagueRepository
from app.repositories.football.match_event_repository import MatchEventRepository
from app.repositories.football.match_repository import MatchRepository
from app.repositories.prediction.model_repository import ModelRepository
from app.repositories.prediction.prediction_repository import PredictionRepository
from app.services.prediction.prediction_service import PredictionService

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")


def _db() -> Session:
    return SessionLocal()


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _compute_standings(matches: list[Match]) -> list[dict]:
    """Calcula tabla de posiciones a partir de partidos finalizados."""
    teams: dict[int, dict] = {}
    for m in matches:
        if m.home_goals is None or m.away_goals is None:
            continue
        for team_id, is_home in [(m.home_team_id, True), (m.away_team_id, False)]:
            if team_id not in teams:
                obj = m.home_team if is_home else m.away_team
                name = obj.name if obj else "?"
                teams[team_id] = {
                    "team_id": team_id,
                    "name": name,
                    "pj": 0, "pg": 0, "pe": 0, "pp": 0,
                    "gf": 0, "gc": 0, "dg": 0, "pts": 0,
                }
            t = teams[team_id]
            t["pj"] += 1
            gf = m.home_goals if is_home else m.away_goals
            gc = m.away_goals if is_home else m.home_goals
            t["gf"] += gf
            t["gc"] += gc
            if gf > gc:
                t["pg"] += 1
                t["pts"] += 3
            elif gf == gc:
                t["pe"] += 1
                t["pts"] += 1
            else:
                t["pp"] += 1
            t["dg"] = t["gf"] - t["gc"]
    return sorted(teams.values(), key=lambda x: (-x["pts"], -x["dg"], -x["gf"]))


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    msg = (
        f"Hola {user.first_name or 'Usuario'}.\n\n"
        "Soy un bot de estadísticas de fútbol con predicciones Dixon-Coles.\n\n"
        "📊 <b>Comandos generales:</b>\n"
        "/ligas — Ligas disponibles\n"
        "/tabla &lt;id_liga&gt; — Tabla de posiciones\n"
        "/partidos [id_liga] — Partidos recientes y próximos\n"
        "/partido &lt;id&gt; — Detalle de un partido\n"
        "/vivo — Partidos en vivo\n\n"
        "🤖 <b>Predicciones y apuestas:</b>\n"
        "/prediccion &lt;id&gt; — Predicción completa\n"
        "/prediccion &lt;id&gt; 1.85 3.40 4.50 — Con análisis de valor\n"
        "/proximos [id_liga] — Próximos partidos con predicción\n"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def cmd_ligas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db()
    try:
        leagues = LeagueRepository(db).list_all()
        if not leagues:
            await update.message.reply_text("No hay ligas en la base de datos.")
            return
        lines = ["⚽ <b>Ligas disponibles</b>\n"]
        for lg in leagues:
            country = f" ({_escape_html(lg.country)})" if lg.country else ""
            lines.append(f"  <b>ID {lg.id}</b>: {_escape_html(lg.name)}{country}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    finally:
        db.close()
        

async def cmd_tabla(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Uso: /tabla <id_liga>\nConsulta /ligas para ver IDs.")
        return
    try:
        league_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("El ID de la liga debe ser un número.")
        return

    db = _db()
    try:
        league = LeagueRepository(db).get_by_id(league_id)
        if not league:
            await update.message.reply_text(f"Liga con ID {league_id} no encontrada.")
            return

        matches = MatchRepository(db).list_finished_by_league(league_id)
        if not matches:
            await update.message.reply_text(
                f"No hay partidos finalizados para {league.name}."
            )
            return

        standings = _compute_standings(matches)

        header = f"📊 Tabla — {_escape_html(league.name)}\n\n"
        lines = [f"{'#':>2} {'Equipo':<20} {'PJ':>3} {'PG':>3} {'PE':>3} {'PP':>3} {'GF':>3} {'GC':>3} {'DG':>4} {'Pts':>4}"]
        lines.append("─" * 64)
        for i, t in enumerate(standings, 1):
            name = t["name"][:18]
            lines.append(
                f"{i:>2} {name:<20} {t['pj']:>3} {t['pg']:>3} {t['pe']:>3} "
                f"{t['pp']:>3} {t['gf']:>3} {t['gc']:>3} {t['dg']:>+4} {t['pts']:>4}"
            )

        table_text = "\n".join(lines)
        # Telegram limit 4096 chars
        if len(header) + len(table_text) + 15 > 4000:
            table_text = table_text[:3900] + "\n…"
        await update.message.reply_text(
            header + "<pre>" + _escape_html(table_text) + "</pre>",
            parse_mode="HTML",
        )
    finally:
        db.close()


async def cmd_partidos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db()
    try:
        now = datetime.now(timezone.utc)
        date_from = now - timedelta(days=1)
        date_to = now + timedelta(days=3)

        league_id: int | None = None
        if context.args:
            try:
                league_id = int(context.args[0])
            except ValueError:
                pass

        matches = MatchRepository(db).list_by_date_range(
            date_from=date_from,
            date_to=date_to,
            league_id=league_id,
        )

        if not matches:
            await update.message.reply_text("No hay partidos en los próximos días.")
            return

        lines = ["⚽ <b>Partidos</b>\n"]
        current_league = ""
        for m in matches[:40]:
            league_name = m.league.name if m.league else "?"
            if league_name != current_league:
                current_league = league_name
                lines.append(f"\n<b>{_escape_html(league_name)}</b>")

            home = _escape_html(m.home_team.name if m.home_team else "?")
            away = _escape_html(m.away_team.name if m.away_team else "?")
            date_str = m.utc_date.strftime("%d/%m %H:%M") if m.utc_date else "?"

            if m.status == "FINISHED" and m.home_goals is not None:
                score = f"{m.home_goals} - {m.away_goals}"
                icon = "✅"
            elif m.status == "IN_PLAY":
                score = f"{m.home_goals or 0} - {m.away_goals or 0}"
                icon = "🔴"
            else:
                score = "vs"
                icon = "🕐"

            lines.append(f"  {icon} {home}  <b>{score}</b>  {away}")
            lines.append(f"      {date_str} UTC · ID: {m.id}")

        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3950] + "\n…"
        await update.message.reply_text(text, parse_mode="HTML")
    finally:
        db.close()
        

async def cmd_partido(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Uso: /partido <id>")
        return
    try:
        match_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("El ID debe ser un número.")
        return

    db = _db()
    try:
        match = MatchRepository(db).get_by_id(match_id)
        if not match:
            await update.message.reply_text(f"Partido {match_id} no encontrado.")
            return

        home = _escape_html(match.home_team.name if match.home_team else "?")
        away = _escape_html(match.away_team.name if match.away_team else "?")
        league = _escape_html(match.league.name if match.league else "?")
        date_str = match.utc_date.strftime("%d/%m/%Y %H:%M UTC") if match.utc_date else "?"

        lines = [
            f"⚽ <b>{home}  vs  {away}</b>",
            f"📊 {league}",
            f"📅 {date_str}",
            f"📍 Estado: {_escape_html(match.status)}",
        ]

        if match.home_goals is not None:
            lines.append(f"⚽ Resultado: <b>{match.home_goals} - {match.away_goals}</b>")
            if match.ht_home_goals is not None:
                lines.append(f"   Medio tiempo: {match.ht_home_goals} - {match.ht_away_goals}")
        if match.round:
            lines.append(f"🔄 Jornada: {_escape_html(match.round)}")
        if match.referee:
            lines.append(f"👤 Árbitro: {_escape_html(match.referee)}")

        events = MatchEventRepository(db).list_by_match(match_id)
        if events:
            lines.append("\n📋 <b>Eventos:</b>")
            for e in events:
                minute_str = f"{e.minute}'" if e.minute is not None else "?"
                team_name = _escape_html(e.team.name) if e.team else ""
                player = _escape_html(e.player_name or "")
                icon = {"GOAL": "⚽", "CARD": "🟨", "SUBSTITUTION": "🔄"}.get(
                    e.event_type, "📌"
                )
                lines.append(f"  {minute_str} {icon} {player} ({team_name})")

        lines.append(f"\n💡 /prediccion {match_id}")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    finally:
        db.close()


async def cmd_vivo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db()
    try:
        matches = MatchRepository(db).list_live()
        if not matches:
            await update.message.reply_text("🔴 No hay partidos en vivo en este momento.")
            return

        lines = ["🔴 <b>Partidos en vivo</b>\n"]
        for m in matches:
            home = _escape_html(m.home_team.name if m.home_team else "?")
            away = _escape_html(m.away_team.name if m.away_team else "?")
            league = _escape_html(m.league.name if m.league else "?")
            hg = m.home_goals or 0
            ag = m.away_goals or 0
            lines.append(f"  {home}  <b>{hg} - {ag}</b>  {away}")
            lines.append(f"  {league} · ID: {m.id}")
            lines.append("")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    finally:
        db.close()


async def cmd_prediccion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Predicción completa. Acepta cuotas opcionales para análisis de valor.

    /prediccion <id>
    /prediccion <id> 1.85 3.40 4.50
    """
    if not context.args:
        await update.message.reply_text(
            "Uso: /prediccion &lt;id&gt; [cuota_L cuota_E cuota_V]\n\n"
            "Ejemplos:\n"
            "  /prediccion 42\n"
            "  /prediccion 42 1.85 3.40 4.50",
            parse_mode="HTML",
        )
        return
    try:
        match_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("El ID debe ser un número.")
        return

    # Parse optional bookmaker odds
    odds: dict[str, float] | None = None
    if len(context.args) >= 4:
        try:
            odds = {
                "home": float(context.args[1]),
                "draw": float(context.args[2]),
                "away": float(context.args[3]),
            }
        except ValueError:
            await update.message.reply_text(
                "Las cuotas deben ser números decimales.\n"
                "Ejemplo: /prediccion 42 1.85 3.40 4.50"
            )
            return

    await update.message.reply_text("⏳ Calculando predicción Dixon-Coles…")

    db = _db()
    try:
        service = PredictionService(db)
        result = service.predict_match(match_id)

        if result is None:
            await update.message.reply_text(
                "No se pudo generar predicción.\n"
                "Verifica que el ID existe y que hay suficientes partidos "
                "históricos en la liga (mínimo 30)."
            )
            return

        home = _escape_html(result["home_team"])
        away = _escape_html(result["away_team"])
        p_h = result["p_home"]
        p_d = result["p_draw"]
        p_a = result["p_away"]

        # Determine winner
        if p_h >= p_d and p_h >= p_a:
            winner, conf = home, p_h
            tip = "1"
        elif p_a >= p_d:
            winner, conf = away, p_a
            tip = "2"
        else:
            winner, conf = "Empate", p_d
            tip = "X"

        lines = [
            "📈 <b>Predicción Dixon-Coles</b>",
            "═══════════════════════",
            f"<b>{home}</b>  vs  <b>{away}</b>",
            f"📊 {_escape_html(result.get('league', ''))}",
        ]

        if result.get("utc_date"):
            lines.append(f"📅 {result['utc_date'].strftime('%d/%m/%Y %H:%M UTC')}")

        lines.append("═══════════════════════")

        # Main tip with confidence
        lines.append(
            f"\n🎯 <b>Predicción: {tip} — {winner}</b> "
            f"({conf * 100:.1f}%)"
        )
        lines.append(f"   Confianza: {_confidence_label(conf)}")

        # 1X2 probabilities with fair odds
        lines.append("\n📊 <b>Mercado 1X2:</b>")
        lines.append(
            f"  🏠 Local:     {p_h * 100:.1f}%  "
            f"(justa: {_prob_to_decimal_odds(p_h)})"
        )
        lines.append(
            f"  🤝 Empate:    {p_d * 100:.1f}%  "
            f"(justa: {_prob_to_decimal_odds(p_d)})"
        )
        lines.append(
            f"  ✈️ Visitante: {p_a * 100:.1f}%  "
            f"(justa: {_prob_to_decimal_odds(p_a)})"
        )

        # xG
        xg_h = result.get("xg_home", 0)
        xg_a = result.get("xg_away", 0)
        lines.append(f"\n⚽ xG: {home} {xg_h:.2f} — {xg_a:.2f} {away}")

        # Over/Under markets
        lines.append("\n📈 <b>Goles totales:</b>")
        if result.get("p_over_1_5") is not None:
            lines.append(
                f"  Over 1.5: {result['p_over_1_5'] * 100:.1f}% | "
                f"Under 1.5: {result['p_under_1_5'] * 100:.1f}%"
            )
        if result.get("p_over_2_5") is not None:
            lines.append(
                f"  Over 2.5: {result['p_over_2_5'] * 100:.1f}% | "
                f"Under 2.5: {result['p_under_2_5'] * 100:.1f}%"
            )
        if result.get("p_over_3_5") is not None:
            lines.append(
                f"  Over 3.5: {result['p_over_3_5'] * 100:.1f}% | "
                f"Under 3.5: {result['p_under_3_5'] * 100:.1f}%"
            )

        # BTTS
        if result.get("p_btts_yes") is not None:
            lines.append(
                f"\n🎯 <b>Ambos marcan:</b> Sí {result['p_btts_yes'] * 100:.1f}% "
                f"| No {result['p_btts_no'] * 100:.1f}%"
            )

        # Double chance
        if result.get("p_1x") is not None:
            lines.append("\n🔀 <b>Doble oportunidad:</b>")
            lines.append(
                f"  1X: {result['p_1x'] * 100:.1f}% | "
                f"X2: {result['p_x2'] * 100:.1f}% | "
                f"12: {result['p_12'] * 100:.1f}%"
            )

        # Top scorelines
        top = result.get("top_scorelines")
        if top:
            lines.append("\n🔢 <b>Marcadores más probables:</b>")
            for score, pct in list(top.items())[:5]:
                lines.append(f"  {score}: {pct}%")

        # ── Value analysis (when bookmaker odds are provided) ──
        if odds:
            lines.append("\n═══════════════════════")
            lines.append("💎 <b>ANÁLISIS DE VALOR</b>")
            lines.append("═══════════════════════")

            markets = [
                ("1 (Local)", p_h, odds["home"]),
                ("X (Empate)", p_d, odds["draw"]),
                ("2 (Visitante)", p_a, odds["away"]),
            ]

            value_found = False
            for name, prob, odd in markets:
                implied = _implied_prob(odd)
                ev = _expected_value(prob, odd)
                kelly = _kelly_fraction(prob, odd)
                edge = prob - implied

                is_value = ev > 0
                icon = "✅" if is_value else "❌"

                lines.append(f"\n{icon} <b>{name}</b> — Cuota: {odd}")
                lines.append(
                    f"   Modelo: {prob * 100:.1f}% vs "
                    f"Casa: {implied * 100:.1f}%"
                )
                lines.append(f"   Ventaja: {edge * 100:+.1f}%")
                lines.append(f"   EV: {ev * 100:+.1f}%")

                if is_value:
                    value_found = True
                    quarter_k = kelly * 0.25
                    lines.append(
                        f"   🏦 Kelly: {kelly * 100:.1f}% "
                        f"(conservador ¼K: {quarter_k * 100:.1f}%)"
                    )

            if value_found:
                lines.append(
                    "\n💡 <i>✅ = Apuesta de valor. "
                    "EV positivo = la cuota de la casa "
                    "supera la probabilidad real.</i>"
                )
            else:
                lines.append(
                    "\n⚠️ <i>No se detectaron apuestas de valor.</i>"
                )

            lines.append(
                "\n⚠️ <i>Kelly% = fracción del bankroll. "
                "Usa ¼K para ser conservador.</i>"
            )
        else:
            lines.append(
                "\n💡 <i>Agrega cuotas para análisis de valor:</i>\n"
                f"/prediccion {match_id} cuota_L cuota_E cuota_V"
            )

        lines.append(f"\n🤖 {result.get('model', 'Dixon-Coles')}")
        if result.get("data_quality"):
            lines.append(f"📋 Datos: {result['data_quality']}")

        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3950] + "\n…"
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception:
        logger.exception("Error en /prediccion %s", match_id)
        await update.message.reply_text("Error al generar la predicción.")
    finally:
        db.close()


# ── Betting helpers ───────────────────────────────────────────────────────

def _prob_to_decimal_odds(prob: float) -> float:
    """Convert probability to fair decimal odds."""
    if prob <= 0:
        return 99.0
    return round(1.0 / prob, 2)


def _implied_prob(decimal_odds: float) -> float:
    """Convert decimal odds to implied probability."""
    if decimal_odds <= 0:
        return 0.0
    return 1.0 / decimal_odds


def _kelly_fraction(prob: float, odds: float) -> float:
    """Kelly criterion: optimal fraction of bankroll.
    f* = (bp - q) / b  where b = odds - 1, p = model prob, q = 1 - p
    """
    b = odds - 1.0
    if b <= 0:
        return 0.0
    q = 1.0 - prob
    f = (b * prob - q) / b
    return max(f, 0.0)


def _expected_value(prob: float, odds: float) -> float:
    """EV = prob * (odds - 1) - (1 - prob).  Positive = value bet."""
    return prob * (odds - 1.0) - (1.0 - prob)


def _confidence_label(prob: float) -> str:
    if prob >= 0.70:
        return "🟢 Alta"
    if prob >= 0.50:
        return "🟡 Media"
    if prob >= 0.35:
        return "🟠 Baja"
    return "🔴 Muy baja"


# ── /proximos ─────────────────────────────────────────────────────────────

async def cmd_proximos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Upcoming matches with pre-computed prediction summaries."""
    db = _db()
    try:
        league_id: int | None = None
        if context.args:
            try:
                league_id = int(context.args[0])
            except ValueError:
                pass

        now = datetime.now(timezone.utc)
        date_to = now + timedelta(days=7)

        matches = MatchRepository(db).list_by_date_range(
            date_from=now,
            date_to=date_to,
            league_id=league_id,
        )
        # Only show scheduled / not-yet-played
        matches = [m for m in matches if m.status in ("SCHEDULED", "NS")]

        if not matches:
            await update.message.reply_text(
                "No hay partidos próximos programados.\n"
                "Ejecuta el worker para sincronizar fixtures."
            )
            return

        # Try to grab cached predictions
        model_rec = ModelRepository(db).get_by_name("dixon_coles_v1")
        pred_repo = PredictionRepository(db)

        lines = ["⚽ <b>Próximos partidos con predicción</b>\n"]
        current_league = ""

        for m in matches[:30]:
            league_name = m.league.name if m.league else "?"
            if league_name != current_league:
                current_league = league_name
                lines.append(f"\n<b>{_escape_html(league_name)}</b>")

            home = _escape_html(m.home_team.name if m.home_team else "?")
            away = _escape_html(m.away_team.name if m.away_team else "?")
            date_str = m.utc_date.strftime("%d/%m %H:%M") if m.utc_date else "?"

            # Check for cached prediction
            pred: Prediction | None = None
            if model_rec:
                pred = pred_repo.latest_for_match_and_model(m.id, model_rec.id)

            if pred:
                ph = pred.p_home * 100
                pd_ = pred.p_draw * 100
                pa = pred.p_away * 100
                # Determine favorite
                if ph >= pd_ and ph >= pa:
                    tip = f"1 ({ph:.0f}%)"
                elif pa >= pd_:
                    tip = f"2 ({pa:.0f}%)"
                else:
                    tip = f"X ({pd_:.0f}%)"
                odds_h = _prob_to_decimal_odds(pred.p_home)
                odds_a = _prob_to_decimal_odds(pred.p_away)
                lines.append(
                    f"  🕐 {home} vs {away}"
                )
                lines.append(
                    f"      {date_str} UTC · <b>Tip: {tip}</b>"
                )
                lines.append(
                    f"      Cuotas justas: {odds_h} / "
                    f"{_prob_to_decimal_odds(pred.p_draw)} / {odds_a}"
                )
            else:
                lines.append(f"  🕐 {home} vs {away}")
                lines.append(f"      {date_str} UTC · Sin predicción aún")

            lines.append(f"      /prediccion {m.id}")

        lines.append(
            "\n💡 <i>Para análisis de valor usa:\n"
            "/prediccion &lt;id&gt; cuota_L cuota_E cuota_V</i>"
        )

        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3950] + "\n…"
        await update.message.reply_text(text, parse_mode="HTML")
    finally:
        db.close()


def main() -> None:
    token = TELEGRAM_BOT_TOKEN
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN no configurado en .env")
        return

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ligas", cmd_ligas))
    app.add_handler(CommandHandler("tabla", cmd_tabla))
    app.add_handler(CommandHandler("partidos", cmd_partidos))
    app.add_handler(CommandHandler("partido", cmd_partido))
    app.add_handler(CommandHandler("vivo", cmd_vivo))
    app.add_handler(CommandHandler("prediccion", cmd_prediccion))
    app.add_handler(CommandHandler("proximos", cmd_proximos))

    logger.info("Bot iniciado — polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
