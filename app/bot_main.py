"""
Bot de Telegram — lee datos de la DB, predice con Dixon-Coles.

Comandos:
    /start              — Bienvenida interactiva con menú inline
    /help               — Ayuda detallada con leyenda de métricas
    /leagues            — Listar ligas disponibles (canónicas, deduplicadas)
    /matches [liga]     — Próximos partidos (opcionalmente filtrar por liga canónica)
    /predict <número>   — Predicción completa del partido seleccionado
    /valuebets          — Top value bets actuales

Mejoras v3 – UX overhaul:
    - Onboarding profesional con InlineKeyboardMarkup
    - /help con leyenda de métricas (Edge, Kelly, confianza)
    - Predicción formateada con secciones claras y leyenda
    - Callback queries para navegación inline (ligas, matches hoy)
    - MessageHandler fallback para texto libre → menú principal
    - Resiliencia: try/except por comando con NetworkError/TimedOut handling
    - Alerta diaria: scheduler envía top value bets al admin
    - Anti-spam: sleep entre envíos masivos
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from dotenv import load_dotenv
from sqlalchemy.orm import Session
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import NetworkError, RetryAfter, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.db.models.football.match import Match
from app.db.session import SessionLocal
from app.services.canonical_league_service import CanonicalLeagueService
from app.services.prediction.prediction_service import PredictionService
from app.services.prediction.value_service import ValueService, compute_kelly_stake, compute_stake_rating

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


# ── Inline keyboard helpers ───────────────────────────────────────────────

def _main_menu_keyboard() -> InlineKeyboardMarkup:
    """Return the persistent main-menu inline keyboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏆 Ligas", callback_data="menu_leagues"),
            InlineKeyboardButton("📅 Partidos de hoy", callback_data="menu_matches"),
        ],
        [
            InlineKeyboardButton("📈 Value Bets", callback_data="menu_valuebets"),
            InlineKeyboardButton("❓ Ayuda", callback_data="menu_help"),
        ],
    ])


# ── /start ────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    name = _esc(user.first_name or "Usuario")
    msg = (
        f"👋 <b>¡Hola {name}!</b>\n\n"
        "Soy <b>FútbolQuant</b> — tu asistente de predicciones de fútbol "
        "basado en el modelo estadístico <b>Dixon-Coles</b>.\n\n"
        "🧠 <b>¿Cómo funciono?</b>\n"
        "Analizo miles de partidos históricos para calcular "
        "<b>probabilidades matemáticas</b> de cada resultado (1X2, O/U, BTTS). "
        "Luego las comparo con las <b>cuotas del mercado</b> para detectar "
        "apuestas con ventaja estadística (<i>value bets</i>).\n\n"
        "📋 <b>Empieza aquí:</b>"
    )
    await _safe_reply(update, msg, parse_mode="HTML", reply_markup=_main_menu_keyboard())


# ── /help ─────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (
        "📖 <b>Guía Rápida</b>\n\n"
        "<b>Flujo recomendado:</b>\n"
        "  1️⃣ /leagues → ver ligas disponibles\n"
        "  2️⃣ /matches &lt;num&gt; → partidos de esa liga\n"
        "  3️⃣ /predict &lt;num&gt; → predicción completa\n"
        "  4️⃣ /valuebets → mejores apuestas de valor\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 <b>Leyenda de métricas:</b>\n\n"
        "🔮 <b>Confianza</b> — Probabilidad del modelo para el resultado más probable.\n"
        "    🟢 Alta (≥70%)  🟡 Media (50-69%)  🟠 Baja (35-49%)  🔴 Muy baja (&lt;35%)\n\n"
        "📈 <b>Edge</b> — Ventaja del modelo sobre las cuotas del mercado.\n"
        "    Si el modelo dice 50% y la casa 40%, el edge es +25%.\n"
        "    Edge ≥ 5% = oportunidad interesante.\n\n"
        "💰 <b>Stake (Kelly)</b> — % recomendado del bankroll según el Criterio de Kelly.\n"
        "    Se usa Kelly fraccionado (10%) con tope del 5% del bankroll.\n"
        "    🟢🟢🟢⚪⚪⚪⚪⚪⚪⚪ = 3/10 (conservador)\n"
        "    🟢🟢🟢🟢🟢🟢🟢🟢⚪⚪ = 8/10 (agresivo)\n\n"
        "📈 <b>xG</b> — Goles esperados: estima cuántos goles debería anotar cada equipo.\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Desarrollado con Dixon-Coles (1997) + calibración Platt + datos de 18 ligas.</i>"
    )
    await _safe_reply(update, msg, parse_mode="HTML", reply_markup=_main_menu_keyboard())


# ── Callback query handler (inline buttons) ──────────────────────────────

async def _callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route inline-keyboard button presses to the right command."""
    query = update.callback_query
    await query.answer()  # Acknowledge immediately to stop loading spinner

    data = query.data or ""
    if data == "menu_leagues":
        await _do_leagues(query.message, context)
    elif data == "menu_matches":
        await _do_matches(query.message, context, canonical_index=None)
    elif data == "menu_valuebets":
        await _do_valuebets(query.message, context)
    elif data == "menu_help":
        msg = (
            "📖 <b>Guía Rápida</b>\n\n"
            "<b>Flujo:</b> /leagues → /matches &lt;num&gt; → /predict &lt;num&gt;\n\n"
            "📈 <b>Edge</b> = ventaja sobre el mercado\n"
            "💰 <b>Stake</b> = % del bankroll (Kelly fraccionado)\n"
            "🟢 Alta confianza · 🟡 Media · 🟠 Baja · 🔴 Muy baja"
        )
        await _safe_edit_or_send(query.message, msg, parse_mode="HTML", reply_markup=_main_menu_keyboard())
    elif data.startswith("league_"):
        idx = int(data.split("_", 1)[1])
        await _do_matches(query.message, context, canonical_index=idx)


async def _safe_edit_or_send(message, text: str, **kwargs) -> None:
    """Try to edit the existing message; fall back to sending a new one."""
    try:
        await message.edit_text(text, **kwargs)
    except Exception:
        try:
            await message.reply_text(text, **kwargs)
        except Exception:
            logger.exception("Failed to edit or send message")


# ── /leagues ──────────────────────────────────────────────────────────────

async def cmd_leagues(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List canonical (deduplicated) leagues."""
    await _do_leagues(update.message, context)


async def _do_leagues(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shared logic for /leagues command and inline button."""
    db = _db()
    try:
        svc = CanonicalLeagueService(db)
        leagues = svc.list_leagues()

        if not leagues:
            await _safe_edit_or_send(message, "⚠️ No hay ligas registradas en la DB.")
            return

        lines = ["🏆 <b>Ligas disponibles</b>\n"]
        keyboard_rows: list[list[InlineKeyboardButton]] = []
        row: list[InlineKeyboardButton] = []

        for lg in leagues:
            country = f" ({_esc(lg.country)})" if lg.country else ""
            status = "✅" if lg.scheduled_matches > 0 else "⏸️"
            lines.append(
                f"  {status} <b>{lg.index}</b> — {_esc(lg.display_name)}{country}\n"
                f"       {lg.finished_matches} jugados · {lg.scheduled_matches} programados"
            )

            # Build inline keyboard: 2 buttons per row
            btn = InlineKeyboardButton(
                f"{lg.index}. {lg.display_name[:18]}",
                callback_data=f"league_{lg.index}",
            )
            row.append(btn)
            if len(row) == 2:
                keyboard_rows.append(row)
                row = []

        if row:
            keyboard_rows.append(row)

        lines.append("\n<i>Toca una liga o usa /matches &lt;num&gt;</i>")
        markup = InlineKeyboardMarkup(keyboard_rows) if keyboard_rows else None

        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3950] + "\n..."
        await _safe_edit_or_send(message, text, parse_mode="HTML", reply_markup=markup)
    except Exception:
        logger.exception("Error en /leagues")
        await _safe_edit_or_send(message, "⚠️ Error al obtener ligas. Intenta de nuevo.")
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

    await _do_matches(update.message, context, canonical_index=canonical_index)


async def _do_matches(message, context: ContextTypes.DEFAULT_TYPE, *, canonical_index: int | None) -> None:
    """Shared logic for /matches command and inline buttons."""
    db = _db()
    try:
        svc = CanonicalLeagueService(db)

        # Auto-ingest if the selected canonical league has no matches
        if canonical_index is not None:
            leagues = svc.list_leagues()
            if canonical_index < 1 or canonical_index > len(leagues):
                await _safe_edit_or_send(
                    message,
                    f"⚠️ Liga fuera de rango. Usa /leagues para ver los números.",
                )
                return
            ingested = svc.auto_ingest_if_empty(canonical_index)
            if ingested:
                logger.info("Auto-ingest: %d partidos nuevos", ingested)

        upcoming = svc.get_upcoming(canonical_index)

        if not upcoming:
            filter_msg = f" para liga {canonical_index}" if canonical_index else ""
            back_btn = InlineKeyboardMarkup([[
                InlineKeyboardButton("🏆 Ver ligas", callback_data="menu_leagues"),
                InlineKeyboardButton("🏠 Menú", callback_data="menu_help"),
            ]])
            await _safe_edit_or_send(
                message,
                f"📭 No hay partidos programados{filter_msg}.",
                reply_markup=back_btn,
            )
            return

        chat_id = message.chat_id
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
        await _safe_edit_or_send(message, text, parse_mode="HTML")
    except Exception:
        logger.exception("Error en /matches")
        await _safe_edit_or_send(message, "⚠️ Error al obtener partidos. Intenta de nuevo.")
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
        "━━━━━━━━━━━━━━━━━━━━━",
        f"⚽ <b>{home}  vs  {away}</b>",
        f"🏆 {_esc(result.league or '')}",
    ]
    if result.utc_date:
        lines.append(f"🕐 {result.utc_date.strftime('%d/%m/%Y %H:%M')} UTC")

    lines.append("━━━━━━━━━━━━━━━━━━━━━")

    # ── Main prediction ──
    lines.append(
        f"\n🔮 <b>Predicción: {tip}</b>\n"
        f"    {_confidence_label(conf)}  ({_pct(conf)})"
    )

    # ── 1X2 visual bar ──
    bar_h = round(p_h * 20)
    bar_d = round(p_d * 20)
    bar_a = 20 - bar_h - bar_d
    bar_a = max(0, bar_a)
    lines.append(
        f"\n📊 <b>Probabilidades 1X2</b>\n"
        f"    🏠 Local    <b>{_pct(p_h)}</b>  {'▓' * bar_h}{'░' * (20 - bar_h)}\n"
        f"    🤝 Empate  <b>{_pct(p_d)}</b>  {'▓' * bar_d}{'░' * (20 - bar_d)}\n"
        f"    ✈️ Visita   <b>{_pct(p_a)}</b>  {'▓' * bar_a}{'░' * (20 - bar_a)}"
    )

    # ── xG ──
    xg_h = result.xg_home or 0
    xg_a = result.xg_away or 0
    if xg_h or xg_a:
        lines.append(
            f"\n📈 <b>Goles Esperados (xG)</b>\n"
            f"    {home} <b>{xg_h:.2f}</b>  —  <b>{xg_a:.2f}</b> {away}"
        )

    # ── Over/Under ──
    ou_parts: list[str] = []
    if result.p_over_1_5 is not None:
        ou_parts.append(f"O1.5 <b>{_pct(result.p_over_1_5)}</b>")
    if result.p_over_2_5 is not None:
        ou_parts.append(f"O2.5 <b>{_pct(result.p_over_2_5)}</b>")
    if result.p_over_3_5 is not None:
        ou_parts.append(f"O3.5 <b>{_pct(result.p_over_3_5)}</b>")
    if ou_parts:
        lines.append(f"\n⬆️ <b>Over/Under</b>\n    {' · '.join(ou_parts)}")

    # ── BTTS ──
    if result.p_btts_yes is not None:
        lines.append(
            f"\n🎯 <b>Ambos Anotan (BTTS)</b>\n"
            f"    Sí <b>{_pct(result.p_btts_yes)}</b>  ·  "
            f"No <b>{_pct(result.p_btts_no)}</b>"
        )

    # ── Double chance ──
    lines.append(
        f"\n🔄 <b>Doble Oportunidad</b>\n"
        f"    1X <b>{_pct(result.p_1x)}</b>  ·  "
        f"X2 <b>{_pct(result.p_x2)}</b>  ·  "
        f"12 <b>{_pct(result.p_12)}</b>"
    )

    # ── Top scorelines ──
    top = result.top_scorelines
    if top:
        scores = [f"<b>{s}</b> ({p}%)" for s, p in list(top.items())[:5]]
        lines.append(f"\n🥅 <b>Marcadores más probables</b>\n    {', '.join(scores)}")

    # ── Footer ──
    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    quality = result.data_quality or ""
    lines.append(f"<i>ℹ️ {quality}\n🔮 Modelo: Dixon-Coles · Calibración Platt</i>")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3950] + "\n..."
    return text


# ── /valuebets ────────────────────────────────────────────────────────────

async def cmd_valuebets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show top value bets (model vs market)."""
    await _do_valuebets(update.message, context)


async def _do_valuebets(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shared logic for /valuebets command and inline button."""
    db = _db()
    try:
        svc = ValueService(db)
        bets = svc.top_value_bets(min_edge=0.03, limit=10)

        if not bets:
            back_btn = InlineKeyboardMarkup([[
                InlineKeyboardButton("🏆 Ver ligas", callback_data="menu_leagues"),
                InlineKeyboardButton("📅 Partidos", callback_data="menu_matches"),
            ]])
            await _safe_edit_or_send(
                message,
                "📭 No hay value bets disponibles.\n"
                "Necesitas odds de mercado cargadas para detectar valor.",
                reply_markup=back_btn,
            )
            return

        text = _format_value_bets(bets, db)
        await _safe_edit_or_send(message, text, parse_mode="HTML")
    except Exception:
        logger.exception("Error en /valuebets")
        await _safe_edit_or_send(
            message,
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

        # Compute stake rating for the best outcome
        outcome_key = best["outcome"]  # "home", "draw", "away"
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
                reply_markup=_main_menu_keyboard(),
            )
        except Exception:
            pass  # can't send — network is likely down


# ── Fallback for free text ────────────────────────────────────────────────

async def _fallback_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle any non-command text message by guiding user to the menu."""
    await _safe_reply(
        update,
        "🤖 No entendí ese mensaje.\n\n"
        "Usa los botones de abajo o escribe un comando:\n"
        "  /leagues · /matches · /predict · /valuebets",
        parse_mode="HTML",
        reply_markup=_main_menu_keyboard(),
    )


# ── main ──────────────────────────────────────────────────────────────────

def main() -> None:
    token = TELEGRAM_BOT_TOKEN
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN no configurado en .env")
        return

    app = Application.builder().token(token).build()

    # Command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("leagues", cmd_leagues))
    app.add_handler(CommandHandler("matches", cmd_matches))
    app.add_handler(CommandHandler("predict", cmd_predict))
    app.add_handler(CommandHandler("valuebets", cmd_valuebets))

    # Inline button handler (main menu + league selection)
    app.add_handler(CallbackQueryHandler(_callback_handler))

    # Fallback: any text that isn't a command → guide to menu
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _fallback_text))

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
