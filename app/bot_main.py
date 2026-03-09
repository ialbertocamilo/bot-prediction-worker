"""
Bot de Telegram — lee datos de la DB, predice con Dixon-Coles.

Comandos:
    /start       — Bienvenida
    /ligas       — Ligas disponibles
    /tabla <id>  — Tabla de posiciones (calculada desde partidos)
    /partidos    — Partidos recientes y próximos
    /partido <id>— Detalle de un partido
    /vivo        — Partidos en vivo
    /prediccion <id> — Predicción Dixon-Coles
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
from app.db.session import SessionLocal
from app.repositories.football.league_repository import LeagueRepository
from app.repositories.football.match_event_repository import MatchEventRepository
from app.repositories.football.match_repository import MatchRepository
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
        "Comandos:\n"
        "/ligas — Ligas disponibles\n"
        "/tabla <id_liga> — Tabla de posiciones\n"
        "/partidos [id_liga] — Partidos recientes y próximos\n"
        "/partido <id> — Detalle de un partido\n"
        "/vivo — Partidos en vivo\n"
        "/prediccion <id> — Predicción Dixon-Coles\n"
    )
    await update.message.reply_text(msg)


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
    if not context.args:
        await update.message.reply_text("Uso: /prediccion <id_partido>")
        return
    try:
        match_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("El ID debe ser un número.")
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
        p_h = result["p_home"] * 100
        p_d = result["p_draw"] * 100
        p_a = result["p_away"] * 100

        if p_h >= p_d and p_h >= p_a:
            winner, conf, icon = home, p_h, "🏠"
        elif p_a >= p_d:
            winner, conf, icon = away, p_a, "✈️"
        else:
            winner, conf, icon = "Empate", p_d, "🤝"

        xg_h = result.get("xg_home", 0)
        xg_a = result.get("xg_away", 0)

        lines = [
            "📈 <b>Predicción Dixon-Coles</b>",
            "─────────────────────",
            f"<b>{home}</b>  vs  <b>{away}</b>",
            f"📊 {_escape_html(result.get('league', ''))}",
            "─────────────────────",
            f"{icon} <b>Predicción: {winner} ({conf:.1f}%)</b>",
            "",
            "Probabilidades 1X2:",
            f"  🏠 Local:     <b>{p_h:.1f}%</b>",
            f"  🤝 Empate:    <b>{p_d:.1f}%</b>",
            f"  ✈️ Visitante: <b>{p_a:.1f}%</b>",
            "",
            f"📉 xG Local: {xg_h:.2f} | xG Visitante: {xg_a:.2f}",
        ]

        if result.get("p_over_2_5") is not None:
            o25 = result["p_over_2_5"] * 100
            u25 = result["p_under_2_5"] * 100
            lines.append(f"⚽ Over 2.5: {o25:.1f}% | Under 2.5: {u25:.1f}%")

        if result.get("p_btts_yes") is not None:
            by = result["p_btts_yes"] * 100
            bn = result["p_btts_no"] * 100
            lines.append(f"🎯 Ambos marcan: Sí {by:.1f}% | No {bn:.1f}%")

        top = result.get("top_scorelines")
        if top:
            lines.append("\n🔢 <b>Marcadores más probables:</b>")
            for score, pct in list(top.items())[:5]:
                lines.append(f"  {score}: {pct}%")

        lines.append(f"\n🤖 Modelo: {result.get('model', 'Dixon-Coles')}")
        if result.get("data_quality"):
            lines.append(f"📋 Datos: {result['data_quality']}")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception:
        logger.exception("Error en /prediccion %s", match_id)
        await update.message.reply_text("Error al generar la predicción.")
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

    logger.info("Bot iniciado — polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
