"""
Bot de Telegram — lee datos de la DB, predice con Dixon-Coles.

Comandos simplificados para usuario final:
    /start           — Bienvenida y ayuda
    /matches         — Lista proximos partidos con numero de opcion
    /predict <num>   — Prediccion completa del partido seleccionado
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
from app.repositories.football.match_repository import MatchRepository
from app.services.prediction.prediction_service import PredictionService

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

# In-memory cache of the latest /matches listing per chat.
# Maps chat_id → list of Match objects in displayed order.
_matches_cache: dict[int, list[Match]] = {}


def _db() -> Session:
    return SessionLocal()


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _confidence_label(prob: float) -> str:
    if prob >= 0.70:
        return "Alta"
    if prob >= 0.50:
        return "Media"
    if prob >= 0.35:
        return "Baja"
    return "Muy baja"


# ── /start ────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    msg = (
        f"Hola {_escape_html(user.first_name or 'Usuario')}!\n\n"
        "Soy tu bot de predicciones de futbol con modelo Dixon-Coles.\n\n"
        "<b>Comandos:</b>\n"
        "/matches  — Ver proximos partidos\n"
        "/predict &lt;numero&gt;  — Prediccion del partido\n\n"
        "<i>Ejemplo: primero usa /matches, luego /predict 1</i>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


# ── /matches ──────────────────────────────────────────────────────────────

async def cmd_matches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List upcoming scheduled matches with a user-friendly number."""
    db = _db()
    try:
        now = datetime.now(timezone.utc)
        date_to = now + timedelta(days=14)

        matches = MatchRepository(db).list_by_date_range(
            date_from=now, date_to=date_to,
        )
        upcoming = [m for m in matches if m.status in ("SCHEDULED", "NS")]

        if not upcoming:
            await update.message.reply_text("No hay partidos programados.")
            return

        # Store in cache so /predict can use the number
        chat_id = update.effective_chat.id
        _matches_cache[chat_id] = upcoming

        lines = ["<b>Proximos partidos</b>\n"]
        current_league = ""

        for idx, m in enumerate(upcoming[:30], 1):
            league_name = m.league.name if m.league else "?"
            if league_name != current_league:
                current_league = league_name
                lines.append(f"\n<b>{_escape_html(league_name)}</b>")

            home = _escape_html(m.home_team.name if m.home_team else "?")
            away = _escape_html(m.away_team.name if m.away_team else "?")
            date_str = m.utc_date.strftime("%d/%m %H:%M") if m.utc_date else "?"
            rnd = f" (J{_escape_html(m.round)})" if m.round else ""

            lines.append(
                f"  <b>{idx}.</b> {home} vs {away}\n"
                f"      {date_str} UTC{rnd}"
            )

        lines.append(
            "\n<i>Usa /predict &lt;numero&gt; para ver la prediccion.\n"
            "Ejemplo: /predict 1</i>"
        )

        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3950] + "\n..."
        await update.message.reply_text(text, parse_mode="HTML")
    finally:
        db.close()


# ── /predict ──────────────────────────────────────────────────────────────

async def cmd_predict(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Predict the match selected by number from /matches."""
    if not context.args:
        await update.message.reply_text(
            "Uso: /predict &lt;numero&gt;\n\n"
            "Primero usa /matches para ver la lista.",
            parse_mode="HTML",
        )
        return

    try:
        choice = int(context.args[0])
    except ValueError:
        await update.message.reply_text("El numero debe ser un entero. Ejemplo: /predict 1")
        return

    chat_id = update.effective_chat.id
    cached = _matches_cache.get(chat_id)
    if not cached:
        await update.message.reply_text(
            "No hay listado activo. Usa /matches primero."
        )
        return

    if choice < 1 or choice > len(cached):
        await update.message.reply_text(
            f"Numero fuera de rango. Elige entre 1 y {len(cached)}."
        )
        return

    match_obj = cached[choice - 1]
    match_id = match_obj.id

    await update.message.reply_text("Calculando prediccion...")

    db = _db()
    try:
        service = PredictionService(db)
        result = service.predict_match(match_id)

        if result is None:
            await update.message.reply_text(
                "No se pudo generar prediccion. "
                "Datos historicos insuficientes (minimo 30 partidos)."
            )
            return

        home = _escape_html(result["home_team"])
        away = _escape_html(result["away_team"])
        p_h = result["p_home"]
        p_d = result["p_draw"]
        p_a = result["p_away"]

        # Determine favorite
        if p_h >= p_d and p_h >= p_a:
            tip, conf = f"1 ({home})", p_h
        elif p_a >= p_d:
            tip, conf = f"2 ({away})", p_a
        else:
            tip, conf = "X (Empate)", p_d

        lines = [
            f"<b>{home}  vs  {away}</b>",
            f"{_escape_html(result.get('league', ''))}",
        ]
        if result.get("utc_date"):
            lines.append(result["utc_date"].strftime("%d/%m/%Y %H:%M UTC"))

        lines.append("")
        lines.append(f"Prediccion: <b>{tip}</b> ({conf * 100:.1f}% — {_confidence_label(conf)})")

        # 1X2
        lines.append(
            f"\n<b>1X2:</b>  Local {p_h * 100:.1f}%  |  Empate {p_d * 100:.1f}%  |  Visitante {p_a * 100:.1f}%"
        )

        # xG
        xg_h = result.get("xg_home", 0)
        xg_a = result.get("xg_away", 0)
        if xg_h or xg_a:
            lines.append(f"<b>xG:</b>  {home} {xg_h:.2f} - {xg_a:.2f} {away}")

        # Over/Under
        ou_parts: list[str] = []
        if result.get("p_over_1_5") is not None:
            ou_parts.append(f"O1.5 {result['p_over_1_5'] * 100:.0f}%")
        if result.get("p_over_2_5") is not None:
            ou_parts.append(f"O2.5 {result['p_over_2_5'] * 100:.0f}%")
        if result.get("p_over_3_5") is not None:
            ou_parts.append(f"O3.5 {result['p_over_3_5'] * 100:.0f}%")
        if ou_parts:
            lines.append(f"<b>Over/Under:</b>  {' | '.join(ou_parts)}")

        # BTTS
        if result.get("p_btts_yes") is not None:
            lines.append(
                f"<b>BTTS:</b>  Si {result['p_btts_yes'] * 100:.0f}%  |  "
                f"No {result['p_btts_no'] * 100:.0f}%"
            )

        # Double chance
        if result.get("p_1x") is not None:
            lines.append(
                f"<b>Doble oportunidad:</b>  "
                f"1X {result['p_1x'] * 100:.0f}%  |  "
                f"X2 {result['p_x2'] * 100:.0f}%  |  "
                f"12 {result['p_12'] * 100:.0f}%"
            )

        # Top scorelines
        top = result.get("top_scorelines")
        if top:
            scores = [f"{s}: {p}%" for s, p in list(top.items())[:5]]
            lines.append(f"<b>Marcadores:</b>  {', '.join(scores)}")

        lines.append(f"\n<i>{result.get('data_quality', '')}</i>")

        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3950] + "\n..."
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception:
        logger.exception("Error en /predict %s (match_id=%s)", choice, match_id)
        await update.message.reply_text("Error al generar la prediccion.")
    finally:
        db.close()


def main() -> None:
    token = TELEGRAM_BOT_TOKEN
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN no configurado en .env")
        return

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("matches", cmd_matches))
    app.add_handler(CommandHandler("predict", cmd_predict))

    logger.info("Bot iniciado — polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
