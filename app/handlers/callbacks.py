from __future__ import annotations

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.services.payments import Gateway

_bot_main = None


def bind_bot_main(bot_main_module) -> None:
    global _bot_main
    _bot_main = bot_main_module


def _main_module():
    if _bot_main is None:
        raise RuntimeError("bot_main module not bound")
    return _bot_main


async def _callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route inline-keyboard button presses to the right command."""
    main = _main_module()

    query = update.callback_query
    await query.answer()  # Acknowledge immediately to stop loading spinner

    # ── Per-user cooldown ─────────────────────────────────────────
    user_id = update.effective_user.id if update.effective_user else 0
    now = main.time.monotonic()
    last = main._user_cooldowns.get(user_id, 0.0)
    if now - last < main._USER_COOLDOWN_SECS:
        return  # silently ignore spam clicks
    main._user_cooldowns[user_id] = now

    data = query.data or ""
    if data == "menu_leagues":
        await main._do_leagues(query.message, context)
    elif data == "menu_matches":
        await main._do_matches(query.message, context, canonical_index=None)
    elif data == "menu_valuebets":
        await main._do_valuebets(query.message, context)
    elif data == "menu_comprar":
        await main._do_comprar_package_select(query, context)
    elif data.startswith("buy_pack_"):
        pack_id = data.removeprefix("buy_pack_")
        pkg = next((p for p in main.CREDIT_PACKAGES if p["id"] == pack_id), None)
        if pkg:
            pen_price = pkg["prices"].get("PEN", 0)
            await main._safe_edit_or_send(
                query.message,
                f"🛒 <b>{pkg['credits']} créditos</b> — S/ {pen_price:.2f}\n\n"
                f"Elige tu método de pago:",
                parse_mode="HTML",
                reply_markup=main._gateway_keyboard_for_pack(pack_id),
            )
        else:
            await main._safe_edit_or_send(query.message, "⚠️ Paquete no encontrado.")
    elif data.startswith("buy_mp_"):
        pack_id = data.removeprefix("buy_mp_")
        await main._do_comprar_checkout(query, context, Gateway.MERCADOPAGO, pack_id)
    elif data.startswith("buy_pp_"):
        pack_id = data.removeprefix("buy_pp_")
        await main._do_comprar_checkout(query, context, Gateway.PAYPAL, pack_id)
    elif data == "menu_back":
        await main._safe_edit_or_send(
            query.message,
            "📋 <b>Menú principal</b>\n\nElige una opción:",
            parse_mode="HTML",
            reply_markup=main._main_menu_keyboard(),
        )
    elif data == "menu_saldo":
        user = query.from_user
        if user:
            db = main._db()
            try:
                repo = main.UserRepository(db)
                balance: int = repo.get_creditos(user.id)
                await main._safe_edit_or_send(
                    query.message,
                    f"💰 <b>Tus créditos:</b> {balance}\n\n",
                    parse_mode="HTML",
                    reply_markup=main._main_menu_keyboard(),
                )
            except Exception:
                main.logger.exception("Error en menu_saldo para user %s", user.id)
                await main._safe_edit_or_send(query.message, "⚠️ Error al consultar créditos.")
            finally:
                db.close()
    elif data == "menu_recargar":
        await main._do_comprar_package_select(query, context)
    elif data == "menu_canjear":
        await main._safe_edit_or_send(
            query.message,
            "🎟️ Para canjear un pin, escribe el comando "
            "/canjear seguido de tu código.\n\n"
            "<b>Ejemplo:</b> <code>/canjear FQ-A1B2-C3D4-E5F6</code>",
            parse_mode="HTML",
            reply_markup=main._main_menu_keyboard(),
        )
    elif data == "menu_ayuda":
        await main._safe_edit_or_send(
            query.message,
            "📖 <b>Guía Rápida</b>\n\n"
            "<b>Flujo:</b> /leagues → /matches &lt;num&gt; → /predict &lt;num&gt;\n\n"
            "📈 <b>Edge</b> = ventaja sobre el mercado\n"
            "💰 <b>Stake</b> = % del bankroll (Kelly fraccionado)\n"
            "🟢 Alta confianza · 🟡 Media · 🟠 Baja · 🔴 Muy baja",
            parse_mode="HTML",
            reply_markup=main._main_menu_keyboard(),
        )
    elif data == "menu_help":
        msg = (
            "📖 <b>Guía Rápida</b>\n\n"
            "<b>Flujo:</b> /leagues → /matches &lt;num&gt; → /predict &lt;num&gt;\n\n"
            "📈 <b>Edge</b> = ventaja sobre el mercado\n"
            "💰 <b>Stake</b> = % del bankroll (Kelly fraccionado)\n"
            "🟢 Alta confianza · 🟡 Media · 🟠 Baja · 🔴 Muy baja"
        )
        await main._safe_edit_or_send(
            query.message,
            msg,
            parse_mode="HTML",
            reply_markup=main._main_menu_keyboard(),
        )
    elif data.startswith("league_"):
        idx = int(data.split("_", 1)[1])
        await main._do_matches(query.message, context, canonical_index=idx)
    elif data == "predict_ask":
        chat_id = query.message.chat_id
        main._awaiting_predict.add(chat_id)
        # Send as a NEW message so the match list stays visible
        await query.message.reply_text(
            "🔮 <b>Predecir partido</b>\n\n"
            "Escribe el <b>número</b> del partido que quieres predecir\n"
            "(según la lista de arriba).\n\n"
            "<i>También puedes usar el comando /predict &lt;número&gt;</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[main._BTN_MATCHES, main._BTN_MENU]]),
        )
    elif data.startswith("odds_"):
        match_id_str = data.split("_", 1)[1]
        chat_id = query.message.chat_id
        pending = main._awaiting_odds.get(chat_id)
        if pending and str(pending["match_id"]) == match_id_str:
            await main._safe_edit_or_send(
                query.message,
                "📝 <b>Ingresa las cuotas de tu casa de apuestas</b>\n\n"
                "Envía los 3 valores separados por espacios:\n"
                "<code>cuota_local cuota_empate cuota_visitante</code>\n\n"
                "<i>Ejemplo: 1.85 3.40 4.50</i>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[main._BTN_MATCHES, main._BTN_MENU]]),
            )
        else:
            await main._safe_edit_or_send(
                query.message,
                "⚠️ Predicción expirada. Usa /predict de nuevo.",
                reply_markup=InlineKeyboardMarkup([[main._BTN_MATCHES, main._BTN_MENU]]),
            )
