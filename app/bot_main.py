"""
Bot de Telegram — lee datos de la DB, predice con Dixon-Coles.

Comandos:
    /start              — Bienvenida y ayuda
    /leagues            — Listar ligas disponibles (canónicas, deduplicadas)
    /matches [liga]     — Próximos partidos (opcionalmente filtrar por liga canónica)
    /predict <número>   — Predicción completa del partido seleccionado
    /valuebets          — Top value bets actuales

Mejoras v2:
    - Resiliencia: try/except por comando con NetworkError/TimedOut handling
    - Alerta diaria: scheduler envía top value bets al admin
    - Formateo profesional: emojis, bloques HTML, info limpia
    - Anti-spam: sleep entre envíos masivos
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from dotenv import load_dotenv
from sqlalchemy.orm import Session
from telegram import Update
from telegram.error import NetworkError, RetryAfter, TimedOut
from telegram.ext import Application, CommandHandler, ContextTypes

from app.db.models.football.match import Match
from app.db.session import SessionLocal
from app.services.canonical_league_service import CanonicalLeagueService
from app.services.prediction.prediction_service import PredictionService
from app.services.prediction.value_service import ValueService

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID: str = os.getenv("ADMIN_CHAT_ID", "")
DAILY_ALERT_HOUR: int = int(os.getenv("DAILY_ALERT_HOUR", "8"))  # UTC

# Anti-spam: minimum seconds between bulk messages (Telegram limit: 30 msg/s)
_BULK_SEND_DELAY = 0.05  # 50ms → max ~20 msg/s, well within limits

# In-memory cache of the latest /matches listing per chat.
_CACHE_TTL_SECS = 900  # 15 minutes
_CACHE_MAX_ENTRIES = 200

_matches_cache: dict[int, tuple[float, list[Match]]] = {}


def _cache_get(chat_id: int) -> list[Match] | None:
    entry = _matches_cache.get(chat_id)
    if entry is None:
        return None
    ts, matches = entry
    if time.monotonic() - ts > _CACHE_TTL_SECS:
        _matches_cache.pop(chat_id, None)
        return None
    return matches


def _cache_set(chat_id: int, matches: list[Match]) -> None:
    if len(_matches_cache) >= _CACHE_MAX_ENTRIES:
        oldest_key = min(_matches_cache, key=lambda k: _matches_cache[k][0])
        _matches_cache.pop(oldest_key, None)
    _matches_cache[chat_id] = (time.monotonic(), matches)


def _db() -> Session:
    return SessionLocal()


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


async def _safe_reply(update: Update, text: str, **kwargs) -> None:
    """Send a reply with automatic retry on Telegram transient errors."""
    try:
        await update.message.reply_text(text, **kwargs)
    except RetryAfter as e:
        logger.warning("Telegram RetryAfter: sleeping %ss", e.retry_after)
        await asyncio.sleep(e.retry_after)
        await update.message.reply_text(text, **kwargs)
    except TimedOut:
        logger.warning("Telegram TimedOut sending reply — retrying once")
        await asyncio.sleep(2)
        await update.message.reply_text(text, **kwargs)
    except NetworkError:
        logger.exception("Telegram NetworkError — message not delivered")


async def _safe_send(bot, chat_id: int | str, text: str, **kwargs) -> None:
    """Send a message to a specific chat with retry on transient errors."""
    try:
        await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except RetryAfter as e:
        logger.warning("Telegram RetryAfter: sleeping %ss", e.retry_after)
        await asyncio.sleep(e.retry_after)
        await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except TimedOut:
        logger.warning("Telegram TimedOut — retrying once")
        await asyncio.sleep(2)
        await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except NetworkError:
        logger.exception("Telegram NetworkError — message not delivered to %s", chat_id)


# ── /start ────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    name = _esc(user.first_name or "Usuario")
    msg = (
        f"👋 ¡Hola <b>{name}</b>!\n\n"
        "Soy tu bot de predicciones de fútbol con modelo Dixon-Coles.\n\n"
        "📋 <b>Comandos:</b>\n"
        "  /leagues  — 🏟️ Ligas disponibles\n"
        "  /matches  — ⚽ Próximos partidos\n"
        "  /matches &lt;num&gt;  — Partidos de una liga\n"
        "  /predict &lt;num&gt;  — 🔮 Predicción del partido\n"
        "  /valuebets  — 📈 Top value bets\n\n"
        "<i>Ejemplo: /leagues → /matches 1 → /predict 1</i>"
    )
    await _safe_reply(update, msg, parse_mode="HTML")


# ── /leagues ──────────────────────────────────────────────────────────────

async def cmd_leagues(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List canonical (deduplicated) leagues."""
    db = _db()
    try:
        svc = CanonicalLeagueService(db)
        leagues = svc.list_leagues()

        if not leagues:
            await _safe_reply(update, "⚠️ No hay ligas registradas en la DB.")
            return

        lines = ["🏟️ <b>Ligas disponibles</b>\n"]
        for lg in leagues:
            country = f" ({_esc(lg.country)})" if lg.country else ""
            status = "✅" if lg.scheduled_matches > 0 else "⏸️"
            lines.append(
                f"  {status} <b>{lg.index}</b> — {_esc(lg.display_name)}{country}\n"
                f"       {lg.finished_matches} jugados · {lg.scheduled_matches} programados"
            )

        lines.append(
            "\n<i>Usa /matches &lt;num&gt; para ver partidos.\n"
            "Ejemplo: /matches 1</i>"
        )

        await _safe_reply(update, "\n".join(lines), parse_mode="HTML")
    except Exception:
        logger.exception("Error en /leagues")
        await _safe_reply(update, "⚠️ Error al obtener ligas. Intenta de nuevo.")
    finally:
        db.close()


# ── /matches ──────────────────────────────────────────────────────────────

async def cmd_matches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List upcoming scheduled matches, optionally filtered by canonical league index."""
    canonical_index: int | None = None
    if context.args:
        try:
            canonical_index = int(context.args[0])
        except ValueError:
            await _safe_reply(
                update,
                "⚠️ Liga inválida. Usa /leagues para ver los números disponibles.",
            )
            return

    db = _db()
    try:
        svc = CanonicalLeagueService(db)

        # Auto-ingest if the selected canonical league has no matches
        if canonical_index is not None:
            leagues = svc.list_leagues()
            if canonical_index < 1 or canonical_index > len(leagues):
                await _safe_reply(
                    update,
                    f"⚠️ Liga fuera de rango. Usa /leagues para ver los números.",
                )
                return
            ingested = svc.auto_ingest_if_empty(canonical_index)
            if ingested:
                logger.info("Auto-ingest: %d partidos nuevos", ingested)

        upcoming = svc.get_upcoming(canonical_index)

        if not upcoming:
            filter_msg = f" para liga {canonical_index}" if canonical_index else ""
            await _safe_reply(
                update,
                f"📭 No hay partidos programados{filter_msg}.",
            )
            return

        chat_id = update.effective_chat.id
        _cache_set(chat_id, upcoming)

        filter_label = ""
        if canonical_index is not None:
            info = svc.list_leagues()[canonical_index - 1]
            filter_label = f" — {info.display_name}"

        lines = [f"⚽ <b>Próximos partidos{_esc(filter_label)}</b>\n"]
        current_league = ""

        for idx, m in enumerate(upcoming[:30], 1):
            league_name = svc.display_name_for(m.league_id)
            if league_name != current_league:
                current_league = league_name
                lines.append(f"\n🏆 <b>{_esc(league_name)}</b>")

            home = _esc(m.home_team.name if m.home_team else "?")
            away = _esc(m.away_team.name if m.away_team else "?")
            date_str = m.utc_date.strftime("%d/%m %H:%M") if m.utc_date else "?"
            rnd = f" (J{_esc(m.round)})" if m.round else ""

            lines.append(
                f"  <b>{idx}.</b> {home} vs {away}\n"
                f"      🕐 {date_str} UTC{rnd}"
            )

        lines.append(
            "\n<i>Usa /predict &lt;num&gt; para ver la predicción.\n"
            "Ejemplo: /predict 1</i>"
        )

        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3950] + "\n..."
        await _safe_reply(update, text, parse_mode="HTML")
    except Exception:
        logger.exception("Error en /matches")
        await _safe_reply(update, "⚠️ Error al obtener partidos. Intenta de nuevo.")
    finally:
        db.close()


# ── /predict ──────────────────────────────────────────────────────────────

async def cmd_predict(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Predict the match selected by number from /matches."""
    if not context.args:
        await _safe_reply(
            update,
            "ℹ️ Uso: /predict &lt;num&gt;\n\n"
            "Primero usa /matches para ver la lista.",
            parse_mode="HTML",
        )
        return

    try:
        choice = int(context.args[0])
    except ValueError:
        await _safe_reply(update, "⚠️ El número debe ser un entero. Ejemplo: /predict 1")
        return

    chat_id = update.effective_chat.id
    cached = _cache_get(chat_id)
    if not cached:
        await _safe_reply(update, "⚠️ No hay listado activo. Usa /matches primero.")
        return

    if choice < 1 or choice > len(cached):
        await _safe_reply(
            update,
            f"⚠️ Número fuera de rango. Elige entre 1 y {len(cached)}.",
        )
        return

    match_obj = cached[choice - 1]
    match_id = match_obj.id

    await _safe_reply(update, "🔄 Calculando predicción...")

    db = _db()
    try:
        service = PredictionService(db)
        result = service.predict_match(match_id)

        if result is None:
            await _safe_reply(
                update,
                "⚠️ No se pudo generar predicción.\n"
                "Datos históricos insuficientes (mínimo 30 partidos).",
            )
            return

        text = _format_prediction(result)
        await _safe_reply(update, text, parse_mode="HTML")
    except Exception:
        logger.exception("Error en /predict %s (match_id=%s)", choice, match_id)
        await _safe_reply(
            update,
            "⚠️ El motor de predicción no pudo procesar la solicitud.\n"
            "Intenta de nuevo en unos minutos.",
        )
    finally:
        db.close()


def _format_prediction(result) -> str:
    """Build a professional HTML-formatted prediction message."""
    home = _esc(result.home_team)
    away = _esc(result.away_team)
    p_h = result.p_home
    p_d = result.p_draw
    p_a = result.p_away

    # Determine favorite
    if p_h >= p_d and p_h >= p_a:
        tip, conf = f"1 ({home})", p_h
    elif p_a >= p_d:
        tip, conf = f"2 ({away})", p_a
    else:
        tip, conf = "X (Empate)", p_d

    lines = [
        f"⚽ <b>{home}  vs  {away}</b>",
        f"🏆 {_esc(result.league or '')}",
    ]
    if result.utc_date:
        lines.append(f"🕐 {result.utc_date.strftime('%d/%m/%Y %H:%M')} UTC")

    lines.append("")
    lines.append(
        f"🔮 Predicción: <b>{tip}</b>\n"
        f"    Confianza: <b>{_pct(conf)}</b> — {_confidence_label(conf)}"
    )

    # ── 1X2 ──
    lines.append(
        f"\n📊 <b>1X2</b>\n"
        f"    Local <b>{_pct(p_h)}</b>  ·  Empate <b>{_pct(p_d)}</b>  ·  Visitante <b>{_pct(p_a)}</b>"
    )

    # ── xG ──
    xg_h = result.xg_home or 0
    xg_a = result.xg_away or 0
    if xg_h or xg_a:
        lines.append(f"📈 <b>xG:</b>  {home} <b>{xg_h:.2f}</b> — <b>{xg_a:.2f}</b> {away}")

    # ── Over/Under ──
    ou_parts: list[str] = []
    if result.p_over_1_5 is not None:
        ou_parts.append(f"O1.5 <b>{_pct(result.p_over_1_5)}</b>")
    if result.p_over_2_5 is not None:
        ou_parts.append(f"O2.5 <b>{_pct(result.p_over_2_5)}</b>")
    if result.p_over_3_5 is not None:
        ou_parts.append(f"O3.5 <b>{_pct(result.p_over_3_5)}</b>")
    if ou_parts:
        lines.append(f"⬆️ <b>Over/Under:</b>  {' · '.join(ou_parts)}")

    # ── BTTS ──
    if result.p_btts_yes is not None:
        lines.append(
            f"🎯 <b>BTTS:</b>  Sí <b>{_pct(result.p_btts_yes)}</b>  ·  "
            f"No <b>{_pct(result.p_btts_no)}</b>"
        )

    # ── Double chance ──
    lines.append(
        f"🔄 <b>Doble oportunidad:</b>\n"
        f"    1X <b>{_pct(result.p_1x)}</b>  ·  "
        f"X2 <b>{_pct(result.p_x2)}</b>  ·  "
        f"12 <b>{_pct(result.p_12)}</b>"
    )

    # ── Top scorelines ──
    top = result.top_scorelines
    if top:
        scores = [f"<b>{s}</b> {p}%" for s, p in list(top.items())[:5]]
        lines.append(f"🥅 <b>Marcadores:</b>  {', '.join(scores)}")

    # ── Data quality footer ──
    lines.append(f"\n<i>ℹ️ {result.data_quality or ''}</i>")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3950] + "\n..."
    return text


# ── /valuebets ────────────────────────────────────────────────────────────

async def cmd_valuebets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show top value bets (model vs market)."""
    db = _db()
    try:
        svc = ValueService(db)
        bets = svc.top_value_bets(min_edge=0.03, limit=10)

        if not bets:
            await _safe_reply(
                update,
                "📭 No hay value bets disponibles.\n"
                "Necesitas odds de mercado cargadas para detectar valor.",
            )
            return

        text = _format_value_bets(bets, db)
        await _safe_reply(update, text, parse_mode="HTML")
    except Exception:
        logger.exception("Error en /valuebets")
        await _safe_reply(
            update,
            "⚠️ Error al obtener value bets. Intenta de nuevo.",
        )
    finally:
        db.close()


def _format_value_bets(bets: list[dict], db: Session) -> str:
    """Build HTML message for top value bets."""
    from app.repositories.football.match_repository import MatchRepository

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

        lines.append(
            f"<b>{i}.</b> {home} vs {away}\n"
            f"    🕐 {date_str} UTC\n"
            f"    💰 Apuesta: <b>{outcome_label}</b>\n"
            f"    📈 Edge: <b>+{edge_pct:.1f}%</b>\n"
            f"    📊 Cuotas: {odds_data['home']:.2f} / {odds_data['draw']:.2f} / {odds_data['away']:.2f}\n"
        )

    lines.append("<i>Edge = ventaja del modelo sobre el mercado (multiplicativa)</i>")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3950] + "\n..."
    return text


# ── Daily alert job ───────────────────────────────────────────────────────

async def _daily_value_bets_alert(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send daily top value bets to the admin chat."""
    if not ADMIN_CHAT_ID:
        return

    db = _db()
    try:
        svc = ValueService(db)
        bets = svc.top_value_bets(min_edge=0.03, limit=5)

        if not bets:
            text = "📭 <b>Reporte diario:</b> No hay value bets disponibles hoy."
        else:
            text = "📅 <b>Reporte diario de Value Bets</b>\n\n" + _format_value_bets(bets, db)

        await _safe_send(context.bot, ADMIN_CHAT_ID, text, parse_mode="HTML")
        # Anti-spam delay after bulk-capable send
        await asyncio.sleep(_BULK_SEND_DELAY)
    except Exception:
        logger.exception("Error en alerta diaria de value bets")
    finally:
        db.close()


# ── Global error handler ─────────────────────────────────────────────────

async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch-all error handler: log and notify user if possible."""
    logger.exception("Unhandled exception in bot handler", exc_info=context.error)

    if isinstance(context.error, (NetworkError, TimedOut)):
        logger.warning("Telegram network issue: %s", context.error)
        return  # transient — don't bother the user

    if isinstance(update, Update) and update.message:
        try:
            await update.message.reply_text(
                "⚠️ Ocurrió un error inesperado. Intenta de nuevo en unos minutos.",
            )
        except Exception:
            pass  # can't send — network is likely down


# ── main ──────────────────────────────────────────────────────────────────

def main() -> None:
    token = TELEGRAM_BOT_TOKEN
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN no configurado en .env")
        return

    app = Application.builder().token(token).build()

    # Command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("leagues", cmd_leagues))
    app.add_handler(CommandHandler("matches", cmd_matches))
    app.add_handler(CommandHandler("predict", cmd_predict))
    app.add_handler(CommandHandler("valuebets", cmd_valuebets))

    # Global error handler — prevents crashes on unhandled exceptions
    app.add_error_handler(_error_handler)

    # Daily value bets alert (via python-telegram-bot's JobQueue)
    if ADMIN_CHAT_ID:
        from datetime import time as dt_time, timezone as tz

        app.job_queue.run_daily(
            _daily_value_bets_alert,
            time=dt_time(hour=DAILY_ALERT_HOUR, minute=0, tzinfo=tz.utc),
            name="daily_value_bets",
        )
        logger.info(
            "Alerta diaria configurada: %02d:00 UTC → chat %s",
            DAILY_ALERT_HOUR, ADMIN_CHAT_ID,
        )

    logger.info("Bot iniciado — polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
