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
from datetime import datetime, timezone as _tz

from dotenv import load_dotenv
from sqlalchemy.orm import Session
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut
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
from app.services.canonical_league_service import (
    CanonicalLeagueService,
    LEAGUE_GROUPS,
    background_ingest,
)
from app.services.prediction.prediction_service import PredictionService
from app.services.prediction.value_service import ValueService, compute_kelly_stake, compute_stake_rating
from app.services.payments import (
    Gateway,
    MercadoPagoProvider,
    PayPalProvider,
    PaymentFactory,
    PaymentService,
)
from app.services.payments.payment_service import CREDITS_PER_PURCHASE
from app.repositories.core.user_repository import UserRepository
from app.services.voucher_service import redeem_voucher
from config import PREDICTION_COST, CREDIT_PACKAGES

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Silence noisy third-party loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID: str = os.getenv("ADMIN_CHAT_ID", "")
DAILY_ALERT_HOUR: int = int(os.getenv("DAILY_ALERT_HOUR", "8"))  # UTC

# Anti-spam: minimum seconds between bulk messages (Telegram limit: 30 msg/s)
_BULK_SEND_DELAY = 0.05  # 50ms → max ~20 msg/s, well within limits

# Staleness threshold for silent background refresh
_STALE_THRESHOLD_SECS: int = 2 * 3600  # 2 hours

# Per-user cooldown: ignore rapid-fire clicks from the same user
_USER_COOLDOWN_SECS: float = 2.0
_user_cooldowns: dict[int, float] = {}

# In-memory cache of the latest /matches listing per chat.
_CACHE_TTL_SECS = 900  # 15 minutes
_CACHE_MAX_ENTRIES = 200

_matches_cache: dict[int, tuple[float, list[Match]]] = {}

# Per-chat state: waiting for odds input after /predict
# chat_id → {"match_id": int, "result": MatchPredictionResult}
_awaiting_odds: dict[int, dict] = {}


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
    """Return the main-menu inline keyboard attached to messages."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏆 Ligas", callback_data="menu_leagues"),
            InlineKeyboardButton("📅 Partidos de hoy", callback_data="menu_matches"),
        ],
        [
            InlineKeyboardButton("💰 Mi Saldo", callback_data="menu_saldo"),
        #    InlineKeyboardButton("💳 Recargar Créditos", callback_data="menu_recargar"),
        ],
        [
            InlineKeyboardButton("🎟️ Canjear Pin", callback_data="menu_canjear"),
            InlineKeyboardButton("🆘 Ayuda", callback_data="menu_ayuda"),
        ],
    ])


# ── /start ────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    name = _esc(user.first_name or "Usuario")
    msg = (
        f"👋 <b>¡Hola {name}!</b>\n\n"
        "Soy <b>TurboPredictions</b> — tu asistente de predicciones de fútbol "
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

    # ── Per-user cooldown ─────────────────────────────────────────
    user_id = update.effective_user.id if update.effective_user else 0
    now = time.monotonic()
    last = _user_cooldowns.get(user_id, 0.0)
    if now - last < _USER_COOLDOWN_SECS:
        return  # silently ignore spam clicks
    _user_cooldowns[user_id] = now

    data = query.data or ""
    if data == "menu_leagues":
        await _do_leagues(query.message, context)
    elif data == "menu_matches":
        await _do_matches(query.message, context, canonical_index=None)
    elif data == "menu_valuebets":
        await _do_valuebets(query.message, context)
    elif data == "menu_comprar":
        await _do_comprar_package_select(query, context)
    elif data.startswith("buy_pack_"):
        pack_id = data.removeprefix("buy_pack_")
        pkg = next((p for p in CREDIT_PACKAGES if p["id"] == pack_id), None)
        if pkg:
            pen_price = pkg["prices"].get("PEN", 0)
            await _safe_edit_or_send(
                query.message,
                f"🛒 <b>{pkg['credits']} créditos</b> — S/ {pen_price:.2f}\n\n"
                f"Elige tu método de pago:",
                parse_mode="HTML",
                reply_markup=_gateway_keyboard_for_pack(pack_id),
            )
        else:
            await _safe_edit_or_send(query.message, "⚠️ Paquete no encontrado.")
    elif data.startswith("buy_mp_"):
        pack_id = data.removeprefix("buy_mp_")
        await _do_comprar_checkout(query, context, Gateway.MERCADOPAGO, pack_id)
    elif data.startswith("buy_pp_"):
        pack_id = data.removeprefix("buy_pp_")
        await _do_comprar_checkout(query, context, Gateway.PAYPAL, pack_id)
    elif data == "menu_back":
        await _safe_edit_or_send(
            query.message,
            "📋 <b>Menú principal</b>\n\nElige una opción:",
            parse_mode="HTML",
            reply_markup=_main_menu_keyboard(),
        )
    elif data == "menu_saldo":
        user = query.from_user
        if user:
            db = _db()
            try:
                repo = UserRepository(db)
                balance: int = repo.get_creditos(user.id)
                await _safe_edit_or_send(
                    query.message,
                    f"💰 <b>Tus créditos:</b> {balance}\n\n",
                    # f"Usa /comprar para adquirir más.",
                    parse_mode="HTML",
                    reply_markup=_main_menu_keyboard(),
                )
            except Exception:
                logger.exception("Error en menu_saldo para user %s", user.id)
                await _safe_edit_or_send(query.message, "⚠️ Error al consultar créditos.")
            finally:
                db.close()
    elif data == "menu_recargar":
        await _do_comprar_package_select(query, context)
    elif data == "menu_canjear":
        await _safe_edit_or_send(
            query.message,
            "🎟️ Para canjear un pin, escribe el comando "
            "/canjear seguido de tu código.\n\n"
            "<b>Ejemplo:</b> <code>/canjear FQ-A1B2-C3D4-E5F6</code>",
            parse_mode="HTML",
            reply_markup=_main_menu_keyboard(),
        )
    elif data == "menu_ayuda":
        await _safe_edit_or_send(
            query.message,
            "📖 <b>Guía Rápida</b>\n\n"
            "<b>Flujo:</b> /leagues → /matches &lt;num&gt; → /predict &lt;num&gt;\n\n"
            "📈 <b>Edge</b> = ventaja sobre el mercado\n"
            "💰 <b>Stake</b> = % del bankroll (Kelly fraccionado)\n"
            "🟢 Alta confianza · 🟡 Media · 🟠 Baja · 🔴 Muy baja",
            parse_mode="HTML",
            reply_markup=_main_menu_keyboard(),
        )
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
    elif data.startswith("odds_"):
        match_id_str = data.split("_", 1)[1]
        chat_id = query.message.chat_id
        pending = _awaiting_odds.get(chat_id)
        if pending and str(pending["match_id"]) == match_id_str:
            await _safe_edit_or_send(
                query.message,
                "📝 <b>Ingresa las cuotas de tu casa de apuestas</b>\n\n"
                "Envía los 3 valores separados por espacios:\n"
                "<code>cuota_local cuota_empate cuota_visitante</code>\n\n"
                "<i>Ejemplo: 1.85 3.40 4.50</i>",
                parse_mode="HTML",
            )
        else:
            await _safe_edit_or_send(
                query.message,
                "⚠️ Predicción expirada. Usa /predict de nuevo.",
            )


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


def _render_matches(
    upcoming: list[Match],
    svc: CanonicalLeagueService,
    filter_label: str = "",
) -> str:
    """Build the HTML text for a list of upcoming matches."""
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
    return text


async def _background_ui_updater(
    *,
    bot: Bot,
    chat_id: int,
    message_id: int,
    canonical_index: int,
    display_name: str,
) -> None:
    """Fire-and-forget: ingest data from provider, then overwrite the
    loading message with the final match list.  Owns its own DB session."""
    try:
        await background_ingest(canonical_index)

        db = SessionLocal()
        try:
            svc = CanonicalLeagueService(db)
            upcoming = svc.get_upcoming(canonical_index)

            if upcoming:
                _cache_set(chat_id, upcoming)
                text = _render_matches(upcoming, svc, f" — {display_name}")
            else:
                text = (
                    f"📭 No hay partidos programados para "
                    f"<b>{_esc(display_name)}</b>."
                )

            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    parse_mode="HTML",
                )
            except BadRequest as exc:
                logger.debug(
                    "edit_message_text failed (chat=%d): %s", chat_id, exc,
                )
        finally:
            db.close()
    except Exception:
        logger.exception(
            "Error en _background_ui_updater (liga %d)", canonical_index,
        )
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    f"❌ Error obteniendo datos para "
                    f"<b>{_esc(display_name)}</b>."
                ),
                parse_mode="HTML",
            )
        except Exception:
            logger.debug("Failed to send error msg to chat %d", chat_id)


def _is_stale(last_ingest: datetime | None) -> bool:
    """Return True if the league data should be refreshed silently."""
    if last_ingest is None:
        return True
    age = (datetime.now(_tz.utc) - last_ingest).total_seconds()
    return age > _STALE_THRESHOLD_SECS


async def _do_matches(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    canonical_index: int | None,
) -> None:
    """Always-Live match handler.

    1. Priority 1 — instant DB read.
    2. Render decision:
       - Matches found → show immediately.
       - 0 matches + league previously ingested → show 'no matches'.
       - Never ingested (last_ingest_at is NULL) → loading + UI updater.
    3. Silent refresh — if data older than 2 h, fire-and-forget
       background_ingest WITHOUT touching the user's message.
    """
    db = _db()
    try:
        svc = CanonicalLeagueService(db)

        # ── Validate index ────────────────────────────────────────────
        if canonical_index is not None:
            leagues = svc.list_leagues()
            if canonical_index < 1 or canonical_index > len(leagues):
                await _safe_edit_or_send(
                    message,
                    "⚠️ Liga fuera de rango. Usa /leagues para ver los números.",
                )
                return

        # ── Fast DB read (microseconds on PostgreSQL) ─────────────────
        upcoming = svc.get_upcoming(canonical_index)
        last_ingest = (
            svc.get_last_ingest_at(canonical_index)
            if canonical_index is not None
            else None
        )

        if upcoming:
            # ── LIVE PATH: data exists → respond instantly ────────────
            _cache_set(message.chat_id, upcoming)

            filter_label = ""
            if canonical_index is not None:
                filter_label = f" — {leagues[canonical_index - 1].display_name}"

            text = _render_matches(upcoming, svc, filter_label)
            await _safe_edit_or_send(message, text, parse_mode="HTML")

            # Silent background refresh if stale (user never notices)
            if canonical_index is not None and _is_stale(last_ingest):
                asyncio.create_task(background_ingest(canonical_index))
            return

        # ── 0 matches — decide between 'no matches' and 'loading' ─────
        if canonical_index is not None:
            info = leagues[canonical_index - 1]

            if last_ingest is not None:
                # League ingested before → genuinely no scheduled games
                await _safe_edit_or_send(
                    message,
                    f"📭 No hay partidos programados para "
                    f"<b>{_esc(info.display_name)}</b>.",
                    parse_mode="HTML",
                )
                # Still refresh silently if stale
                if _is_stale(last_ingest):
                    asyncio.create_task(background_ingest(canonical_index))
                return

            # Never ingested → true cold start with UI updater
            await _safe_edit_or_send(
                message,
                f"⏳ Obteniendo datos por primera vez para "
                f"<b>{_esc(info.display_name)}</b>.\n"
                "Este mensaje se actualizará automáticamente.",
                parse_mode="HTML",
            )
            asyncio.create_task(
                _background_ui_updater(
                    bot=context.bot,
                    chat_id=message.chat_id,
                    message_id=message.message_id,
                    canonical_index=canonical_index,
                    display_name=info.display_name,
                )
            )
            return

        # No canonical_index and no data → empty state
        back_btn = InlineKeyboardMarkup([[
            InlineKeyboardButton("🏆 Ver ligas", callback_data="menu_leagues"),
            InlineKeyboardButton("🏠 Menú", callback_data="menu_help"),
        ]])
        await _safe_edit_or_send(
            message,
            "📭 No hay partidos programados.",
            parse_mode="HTML",
            reply_markup=back_btn,
        )
    except Exception:
        logger.exception("Error en /matches")
        await _safe_edit_or_send(message, "⚠️ Error al obtener partidos. Intenta de nuevo.")
    finally:
        db.close()


# ── /predict ──────────────────────────────────────────────────────────────

async def cmd_predict(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Predict the match selected by number from /matches.

    Credit flow:
      1. SELECT … FOR UPDATE → lock user row
      2. Fail-fast if creditos <= 0
      3. Run prediction (heavy CPU work)
      4. On success → deduct 1 credit, COMMIT
      5. On failure → ROLLBACK (no charge)
    """
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
    match_id: int = match_obj.id
    user = update.effective_user
    if not user:
        return
    telegram_id: int = user.id

    # ── 1. Validación de créditos con bloqueo de fila ─────────────────
    db = _db()
    try:
        user_repo = UserRepository(db)
        user_row = user_repo.get_for_update(telegram_id)

        if user_row is None:
            user_repo.get_or_create(telegram_id=telegram_id, username=user.username)
            user_row = user_repo.get_for_update(telegram_id)

        if user_row is None or user_row.creditos < PREDICTION_COST:
            db.rollback()
            buy_link: str = ""
            try:
                provider = PaymentFactory.create(Gateway.MERCADOPAGO)
                pay_svc = PaymentService(_db(), provider)
                buy_link = await pay_svc.create_checkout(telegram_id)
            except Exception:
                logger.debug("Could not generate payment link for insufficient-credits msg")

            # link_text: str = buy_link if buy_link else "/comprar"
            await _safe_reply(
                update,
                f"⚠️ Saldo insuficiente. Necesitas <b>{PREDICTION_COST}</b> créditos "
                f"(tienes <b>{user_row.creditos if user_row else 0}</b>).\n",
                # f"Compra más créditos aquí: {link_text}",
                parse_mode="HTML",
            )
            return

        # ── 2. Ejecutar predicción (CPU-heavy) ───────────────────────
        await _safe_reply(update, "🔄 Calculando predicción...")

        result = None
        try:
            service = PredictionService(db)
            result = service.predict_match(match_id)
        except Exception:
            logger.exception("Error en /predict %s (match_id=%s)", choice, match_id)
            db.rollback()
            await _safe_reply(
                update,
                "⚠️ El motor de predicción falló. No se descontaron créditos.\n"
                "Intenta de nuevo en unos minutos.",
            )
            return

        if result is None:
            db.rollback()
            await _safe_reply(
                update,
                "⚠️ No se pudo generar predicción.\n"
                "Datos históricos insuficientes (mínimo 30 partidos).\n"
                "No se descontaron créditos.",
            )
            return

        # ── 3. Predicción exitosa → descontar créditos y commit ────
        try:
            new_balance: int = user_repo.deduct_credito(telegram_id, amount=PREDICTION_COST)
            db.commit()
        except Exception:
            logger.exception("DB error deducting credit for tg_id=%d", telegram_id)
            db.rollback()
            await _safe_reply(
                update,
                "⚠️ Error interno al descontar créditos. No se realizó el cobro.\n"
                "Intenta de nuevo.",
            )
            return

        logger.info(
            "/predict tg_id=%d match=%d → -%d créditos (balance=%d)",
            telegram_id, match_id, PREDICTION_COST, new_balance,
        )

        # ── 4. Enviar resultado al usuario ────────────────────────────
        _awaiting_odds[chat_id] = {"match_id": match_id, "result": result}

        # Send team badges if available
        match_db = db.get(Match, match_id)
        home_crest = (
            match_db.home_team.crest_url
            if match_db and match_db.home_team else None
        )
        away_crest = (
            match_db.away_team.crest_url
            if match_db and match_db.away_team else None
        )
        if home_crest and away_crest:
            try:
                await update.message.reply_media_group(
                    media=[
                        InputMediaPhoto(media=home_crest),
                        InputMediaPhoto(media=away_crest),
                    ],
                )
            except Exception:
                logger.debug("Could not send team badges")
        elif home_crest or away_crest:
            try:
                await update.message.reply_photo(photo=home_crest or away_crest)
            except Exception:
                logger.debug("Could not send team badge")

        text = _format_prediction(result)
        credit_footer: str = f"\n\n💰 Créditos restantes: <b>{new_balance}</b>"
        odds_btn = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "💰 Ingresar cuotas de casa de apuestas",
                callback_data=f"odds_{match_id}",
            )],
        ])
        await _safe_reply(
            update, text + credit_footer, parse_mode="HTML", reply_markup=odds_btn,
        )
    except Exception:
        logger.exception("Unhandled error in /predict %s (match_id=%s)", choice, match_id)
        db.rollback()
        await _safe_reply(
            update,
            "⚠️ Error inesperado. No se descontaron créditos.\n"
            "Intenta de nuevo en unos minutos.",
        )
    finally:
        db.close()


def _format_prediction(result) -> str:
    """Build a clean, user-focused prediction message."""
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
        f"    🏠 {home}  <b>{_pct(p_h)}</b>  {'▓' * bar_h}{'░' * (20 - bar_h)}\n"
        f"    🤝 Empate  <b>{_pct(p_d)}</b>  {'▓' * bar_d}{'░' * (20 - bar_d)}\n"
        f"    ✈️ {away}  <b>{_pct(p_a)}</b>  {'▓' * bar_a}{'░' * (20 - bar_a)}"
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

    # ── Double Chance ──
    lines.append(
        f"\n🔄 <b>Doble Oportunidad</b>\n"
        f"    1X — {home} o Empate: <b>{_pct(result.p_1x)}</b>\n"
        f"    X2 — {away} o Empate: <b>{_pct(result.p_x2)}</b>\n"
        f"    12 — {home} o {away}: <b>{_pct(result.p_12)}</b>"
    )

    # ── Top scorelines ──
    top = result.top_scorelines
    if top:
        scores = [f"<b>{s}</b> ({p}%)" for s, p in list(top.items())[:3]]
        lines.append(f"\n🥅 <b>Marcadores más probables</b>\n    {', '.join(scores)}")

    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    lines.append("<i>💡 Toca el botón de abajo para analizar con tus cuotas.</i>")

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

    # Glossary
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


# ── /comprar ──────────────────────────────────────────────────────────────

def _package_selection_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard with one button per credit package."""
    buttons = []
    for pkg in CREDIT_PACKAGES:
        pen_price = pkg["prices"].get("PEN", 0)
        buttons.append([InlineKeyboardButton(
            f"📦 {pkg['credits']} créditos — S/ {pen_price:.2f}",
            callback_data=f"buy_pack_{pkg['id']}",
        )])
    buttons.append([InlineKeyboardButton("🔙 Volver", callback_data="menu_back")])
    return InlineKeyboardMarkup(buttons)


def _gateway_keyboard_for_pack(pack_id: str) -> InlineKeyboardMarkup:
    """Inline keyboard with gateway options for a specific package."""
    mp = PaymentFactory.create(Gateway.MERCADOPAGO)
    pp = PaymentFactory.create(Gateway.PAYPAL)
    # Find package to show per-gateway price
    pkg = next((p for p in CREDIT_PACKAGES if p["id"] == pack_id), None)
    if pkg:
        mp_price = pkg["prices"].get(mp.pricing.currency, 0)
        pp_price = pkg["prices"].get(pp.pricing.currency, 0)
        mp_label = f"S/ {mp_price:.2f}" if mp.pricing.currency == "PEN" else f"${mp_price:.2f}"
        pp_label = f"${pp_price:.2f}" if pp.pricing.currency == "USD" else f"S/ {pp_price:.2f}"
    else:
        mp_label = mp.pricing.label
        pp_label = pp.pricing.label
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"🇵🇪 Mercado Pago — {mp_label}",
            callback_data=f"buy_mp_{pack_id}",
        )],
        [InlineKeyboardButton(
            f"🌎 PayPal — {pp_label}",
            callback_data=f"buy_pp_{pack_id}",
        )],
        [InlineKeyboardButton("🔙 Volver", callback_data="menu_recargar")],
    ])


async def _do_comprar_package_select(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show available credit packages (inline callback)."""
    await _safe_edit_or_send(
        query.message,
        "🛒 <b>Comprar créditos</b>\n\n"
        "Elige el paquete que deseas:",
        parse_mode="HTML",
        reply_markup=_package_selection_keyboard(),
    )


async def _do_comprar_checkout(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    gateway: Gateway,
    pack_id: str,
) -> None:
    """Generate a checkout link for the chosen gateway + package."""
    user = query.from_user
    if not user:
        return

    pkg = next((p for p in CREDIT_PACKAGES if p["id"] == pack_id), None)
    if not pkg:
        await _safe_edit_or_send(query.message, "⚠️ Paquete no encontrado.")
        return

    db = _db()
    try:
        provider = PaymentFactory.create(gateway)
        currency = provider.pricing.currency
        price = pkg["prices"].get(currency)
        if price is None:
            await _safe_edit_or_send(
                query.message,
                "⚠️ Precio no disponible para esta pasarela.",
            )
            return
        credits = pkg["credits"]

        svc = PaymentService(db, provider)
        init_point: str = await svc.create_checkout(
            user.id, credits=credits, price=price,
        )
        if not init_point:
            await _safe_edit_or_send(
                query.message,
                "⚠️ No se pudo generar el link de pago.",
            )
            return

        if currency == "PEN":
            price_label = f"S/ {price:.2f}"
        else:
            price_label = f"${price:.2f}"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                f"💳 Pagar {price_label} → {credits} créditos",
                url=init_point,
            )],
        ])
        await _safe_edit_or_send(
            query.message,
            f"🛒 <b>Comprar créditos</b>\n\n"
            f"📦 <b>{credits} créditos</b> por "
            f"<b>{price_label}</b>\n\n"
            f"Toca el botón para ir a pagar.",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except Exception:
        logger.exception("Error en checkout %s pack %s", gateway.value, pack_id)
        await _safe_edit_or_send(
            query.message,
            "⚠️ Error al conectar con la pasarela de pago.",
        )
    finally:
        db.close()


async def cmd_comprar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra la selección de paquetes (/comprar)."""
    user = update.effective_user
    if not user:
        return

    await _safe_reply(
        update,
        "🛒 <b>Comprar créditos</b>\n\n"
        "Elige el paquete que deseas:",
        parse_mode="HTML",
        reply_markup=_package_selection_keyboard(),
    )


# ── /creditos ─────────────────────────────────────────────────────────────

async def cmd_creditos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra el saldo de créditos del usuario."""
    user = update.effective_user
    if not user:
        return

    db = _db()
    try:
        repo = UserRepository(db)
        balance: int = repo.get_creditos(user.id)
        await _safe_reply(
            update,
            f"💰 <b>Tus créditos:</b> {balance}\n\n",
            # f"Usa /comprar para adquirir más.",
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Error en /creditos para user %s", user.id)
        await _safe_reply(update, "⚠️ Error al consultar créditos.")
    finally:
        db.close()


# ── /canjear ──────────────────────────────────────────────────────────────

async def cmd_canjear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Redeem a credit voucher: /canjear XXXX-XXXX-XXXX-XXXX"""
    user = update.effective_user
    if not user:
        return

    if not context.args:
        await _safe_reply(update, "❌ Formato incorrecto. Uso: /canjear CODIGO")
        return

    codigo_limpio = context.args[0].strip().upper()

    db = _db()
    try:
        creditos, nuevo_saldo = redeem_voucher(db=db, telegram_id=user.id, code=codigo_limpio)
        await _safe_reply(
            update,
            f"✅ ¡Canje exitoso! Se sumaron <b>{creditos}</b> créditos a tu cuenta.\n"
            f"Tu nuevo saldo es: <b>{nuevo_saldo}</b>",
            parse_mode="HTML",
        )
    except ValueError as exc:
        await _safe_reply(update, f"❌ Error al canjear: {exc}")
    except Exception:
        logger.exception("Error en /canjear para user %s", user.id)
        await _safe_reply(update, "⚠️ Error inesperado al canjear el voucher.")
    finally:
        db.close()


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
    """Handle free text: odds input or fallback to menu."""
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    # Check if user is providing odds
    pending = _awaiting_odds.get(chat_id)
    if pending:
        parts = text.replace(",", ".").split()
        if len(parts) == 3:
            try:
                h_odds = float(parts[0])
                d_odds = float(parts[1])
                a_odds = float(parts[2])
                if h_odds < 1.01 or d_odds < 1.01 or a_odds < 1.01:
                    raise ValueError("Odds must be > 1.0")
                if h_odds > 1000 or d_odds > 1000 or a_odds > 1000:
                    raise ValueError("Odds unreasonably high")
            except ValueError:
                await _safe_reply(
                    update,
                    "⚠️ Formato inválido. Las cuotas deben ser números mayores a 1.0\n"
                    "<i>Ejemplo: 1.85 3.40 4.50</i>",
                    parse_mode="HTML",
                )
                return

            result = pending["result"]
            _awaiting_odds.pop(chat_id, None)

            # Send prediction + stake analysis together so match info stays visible
            prediction_text = _format_prediction(result)
            analysis = _format_stake_analysis(result, h_odds, d_odds, a_odds)
            combined = prediction_text + "\n\n" + analysis
            if len(combined) > 4000:
                # If too long, send as two separate messages
                await _safe_reply(update, prediction_text, parse_mode="HTML")
                await _safe_reply(update, analysis, parse_mode="HTML", reply_markup=_main_menu_keyboard())
            else:
                await _safe_reply(update, combined, parse_mode="HTML", reply_markup=_main_menu_keyboard())
            return

    await _safe_reply(
        update,
        "🤖 No entendí ese mensaje.\n\n"
        "Usa los botones de abajo o escribe un comando:\n"
        "  /leagues · /matches · /predict · /valuebets",
        parse_mode="HTML",
        reply_markup=_main_menu_keyboard(),
    )


# ── Cache Pre-warming ─────────────────────────────────────────────────────

_HYDRATION_DELAY_SECONDS: int = 15
_HYDRATION_INTERVAL_SECONDS: int = 4 * 3600  # 4 hours


async def _hydrate_all_leagues() -> None:
    """Iterate every configured league and ingest data with throttling.

    Each league ingestion is isolated (its own DB session inside
    ``background_ingest``).  A 15-second delay between leagues prevents
    the ESPN scraper from being rate-limited.
    """
    total: int = len(LEAGUE_GROUPS)
    logger.info("Hydration: starting for %d leagues", total)
    for idx in range(1, total + 1):
        try:
            await background_ingest(idx)
        except Exception:
            logger.exception("Hydration error for league index %d", idx)
        if idx < total:
            await asyncio.sleep(_HYDRATION_DELAY_SECONDS)
    logger.info("Hydration: complete for %d leagues", total)


async def _post_init(app: Application) -> None:
    """post_init hook — launch cache pre-warming as a fire-and-forget task.

    The task runs entirely in the background; the bot starts accepting
    messages immediately without waiting for hydration to finish.
    """
    asyncio.create_task(_hydrate_all_leagues(), name="cache-prewarm")
    logger.info("Post-init: cache pre-warming task launched (non-blocking)")


async def _scheduled_hydration(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback — periodic re-hydration of all leagues."""
    asyncio.create_task(_hydrate_all_leagues(), name="scheduled-hydration")


# ── main ──────────────────────────────────────────────────────────────────

def main() -> None:
    token = TELEGRAM_BOT_TOKEN
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN no configurado en .env")
        return

    app = Application.builder().token(token).post_init(_post_init).build()

    # Command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("leagues", cmd_leagues))
    app.add_handler(CommandHandler("matches", cmd_matches))
    app.add_handler(CommandHandler("predict", cmd_predict))
    app.add_handler(CommandHandler("valuebets", cmd_valuebets))
    app.add_handler(CommandHandler("comprar", cmd_comprar))
    app.add_handler(CommandHandler("creditos", cmd_creditos))
    app.add_handler(CommandHandler("canjear", cmd_canjear))

    # Inline button handler (main menu + league selection)
    app.add_handler(CallbackQueryHandler(_callback_handler))

    # Fallback: any text that isn't a command → guide to menu
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _fallback_text))

    # Global error handler — prevents crashes on unhandled exceptions
    app.add_error_handler(_error_handler)

    # Recurring hydration every 4 hours (first run deferred — post_init handles startup)
    app.job_queue.run_repeating(
        _scheduled_hydration,
        interval=_HYDRATION_INTERVAL_SECONDS,
        first=_HYDRATION_INTERVAL_SECONDS,
        name="hydrate_all_leagues",
    )
    logger.info(
        "Hydration job scheduled: every %d h",
        _HYDRATION_INTERVAL_SECONDS // 3600,
    )

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
