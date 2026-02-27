"""
Bot de Telegram: estadísticas de fútbol y predicción de ganador según datos.
Comandos: /start, /ligas, /tabla, /partidos, /partido, /vivo, /prediccion
"""
import logging
from datetime import datetime, timezone
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from config import TELEGRAM_BOT_TOKEN, FOOTBALL_API_TOKEN
from football_api import FootballAPI, FootballAPIError
from predictor import predict_match
from crest_image import build_match_image

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Cliente API (se usa en handlers)
football_api = FootballAPI(FOOTBALL_API_TOKEN) if FOOTBALL_API_TOKEN else None

# Cache de área por team_id (evita llamadas repetidas a la API)
_team_area_cache: dict[int, str | None] = {}

# Código de área (3 letras) -> bandera emoji
AREA_TO_FLAG = {
    "ESP": "🇪🇸", "ITA": "🇮🇹", "DEU": "🇩🇪", "GER": "🇩🇪", "FRA": "🇫🇷",
    "GBR": "🇬🇧", "ENG": "🇬🇧", "SCO": "🇬🇧", "NED": "🇳🇱", "POR": "🇵🇹",
    "BRA": "🇧🇷", "ARG": "🇦🇷", "MEX": "🇲🇽", "URU": "🇺🇾", "BEL": "🇧🇪",
    "CRO": "🇭🇷", "SUI": "🇨🇭", "TUR": "🇹🇷", "GRE": "🇬🇷", "RUS": "🇷🇺",
    "UKR": "🇺🇦", "POL": "🇵🇱", "AUT": "🇦🇹", "CZE": "🇨🇿", "SRB": "🇷🇸",
    "ROU": "🇷🇴", "BUL": "🇧🇬", "HUN": "🇭🇺", "SWE": "🇸🇪", "NOR": "🇳🇴",
    "DEN": "🇩🇰", "IRL": "🇮🇪", "WAL": "🇬🇧", "SEN": "🇸🇳", "MAR": "🇲🇦",
    "TUN": "🇹🇳", "EGY": "🇪🇬", "NGA": "🇳🇬", "CIV": "🇨🇮", "GHA": "🇬🇭",
    "JPN": "🇯🇵", "KOR": "🇰🇷", "AUS": "🇦🇺", "USA": "🇺🇸", "CAN": "🇨🇦",
    "CHI": "🇨🇱", "COL": "🇨🇴", "ECU": "🇪🇨", "PER": "🇵🇪", "VEN": "🇻🇪",
    "SAU": "🇸🇦", "IRN": "🇮🇷", "QAT": "🇶🇦", "UAE": "🇦🇪", "ISR": "🇮🇱",
}


def _flag_for_area(area_code: str | None) -> str:
    """Devuelve emoji de bandera para código de área (ej. ESP, ENG)."""
    if not area_code:
        return "🏳️"
    return AREA_TO_FLAG.get(area_code.upper(), "🏳️")


def _get_team_flag(api: FootballAPI, team_id: int) -> str:
    """Obtiene la bandera del equipo (usa cache)."""
    if team_id in _team_area_cache:
        return _flag_for_area(_team_area_cache[team_id])
    try:
        team = api.get_team(team_id)
        area = (team.get("area") or {}).get("code")
        _team_area_cache[team_id] = area
        return _flag_for_area(area)
    except FootballAPIError:
        _team_area_cache[team_id] = None
        return "🏳️"


def _escape_html(s: str) -> str:
    """Escapa &, <, > para usar dentro de HTML en Telegram."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_md(text: str) -> str:
    """Escapa caracteres especiales para MarkdownV2 (simplificado)."""
    for c in "_*[]()~`>#+-=|{}.!":
        text = text.replace(c, f"\\{c}")
    return text


# Solo estos estados = partido realmente en juego. FINISHED, TIMED, etc. no cuentan.
LIVE_STATUSES = ("IN_PLAY", "PAUSED")

# Si la API no actualiza el estado a FINISHED, no mostrar como vivo si lastUpdated es más viejo (minutos).
MAX_LIVE_AGE_MINUTES = 10
# Si el minuto es >= este valor, asumir que el partido ya terminó (la API a veces sigue en IN_PLAY).
MAX_MINUTE_STILL_LIVE = 96
# Minutos tras el inicio para considerar que el partido "debió terminar" (90 + descanso + margen).
MATCH_END_MARGIN_MINUTES = 105
# Cuántos partidos "pasados" refrescar con get_match en /partidos (límite por rate limit).
MAX_REFRESH_STALE_MATCHES = 5


def _match_utc_datetime(m: dict) -> datetime | None:
    """Fecha/hora UTC del partido. None si no se puede parsear."""
    s = m.get("utcDate")
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _match_should_have_ended(m: dict) -> bool:
    """True si el partido ya debería haber finalizado (más de MATCH_END_MARGIN_MINUTES desde el inicio)."""
    dt = _match_utc_datetime(m)
    if not dt:
        return False
    return (datetime.now(timezone.utc) - dt).total_seconds() > MATCH_END_MARGIN_MINUTES * 60


def _refresh_stale_matches(api: FootballAPI, matches: list) -> list:
    """
    Para partidos que ya deberían haber terminado pero no están FINISHED,
    re-pide el detalle (get_match) para obtener resultado actualizado. Máximo MAX_REFRESH_STALE_MATCHES.
    """
    refreshed_by_id: dict[int, dict] = {}
    for m in matches:
        if len(refreshed_by_id) >= MAX_REFRESH_STALE_MATCHES:
            break
        status = (m.get("status") or "").upper()
        if status == "FINISHED":
            continue
        if not _match_should_have_ended(m):
            continue
        mid = m.get("id")
        if not mid or mid in refreshed_by_id:
            continue
        try:
            full = api.get_match(mid)
            refreshed_by_id[mid] = full
        except FootballAPIError:
            continue
    if not refreshed_by_id:
        return matches
    out = []
    for m in matches:
        mid = m.get("id")
        if mid in refreshed_by_id:
            out.append(refreshed_by_id[mid])
        else:
            out.append(m)
    return out


def _is_live(status: str) -> bool:
    """True si el partido está en vivo (jugándose o descanso). Excluye FINISHED y el resto."""
    return (status or "").upper() in LIVE_STATUSES


def _match_last_updated_ago_seconds(m: dict) -> float | None:
    """Segundos desde lastUpdated del partido. None si no hay lastUpdated o no se puede parsear."""
    s = m.get("lastUpdated")
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        updated = datetime.fromisoformat(s)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - updated).total_seconds()
    except Exception:
        return None


def _is_reliably_live(m: dict, max_age_minutes: int = MAX_LIVE_AGE_MINUTES) -> bool:
    """
    True solo si el partido está en vivo Y los datos tienen sentido.
    - La API a veces deja IN_PLAY en partidos ya finalizados: si minute >= 97, no considerar en vivo.
    - Si lastUpdated es viejo, tampoco confiar en IN_PLAY/PAUSED.
    """
    if not _is_live(m.get("status", "")):
        return False
    minute = m.get("minute")
    if minute is not None and minute >= MAX_MINUTE_STILL_LIVE:
        return False  # En la práctica el partido ya terminó; la API no actualiza a FINISHED
    ago = _match_last_updated_ago_seconds(m)
    if ago is None:
        return True  # Sin lastUpdated, confiar en el estado (y en el minuto)
    return ago <= max_age_minutes * 60


def _live_minute_text(m: dict) -> str:
    """Texto del minuto o estado para partidos en vivo (incluye tiempo añadido si existe)."""
    status = (m.get("status") or "").upper()
    if status == "PAUSED":
        return "Descanso"
    minute = m.get("minute")
    if minute is not None:
        injury = m.get("injuryTime")
        if injury not in (None, 0):
            return f"{minute}+{injury}'"
        return f"{minute}'"
    return "En vivo"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mensaje de bienvenida y comandos disponibles."""
    user = update.effective_user
    msg = (
        f"Hola {user.first_name or 'Usuario'}.\n\n"
        "Soy un bot de estadísticas de fútbol y predicciones.\n\n"
        "Comandos:\n"
        "/ligas — Ver competiciones disponibles\n"
        "/tabla (código) — Tabla de posiciones. Ej: /tabla PL\n"
        "/partidos (código) — Partidos de hoy o de una liga\n"
        "/vivo — Partidos en vivo ahora (resultados al momento)\n"
        "/partido (id) — Detalle de un partido\n"
        "/prediccion (id) — Predicción del ganador según estadísticas\n\n"
        "Para usar tablas, partidos y predicciones necesitas una API key gratuita "
        "de football-data.org (registro en su web). Sin ella solo /ligas tendrá datos limitados."
    )
    await update.message.reply_text(msg)


async def cmd_ligas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lista competiciones disponibles."""
    if not football_api or not FOOTBALL_API_TOKEN:
        await update.message.reply_text(
            "No está configurada la API de fútbol (FOOTBALL_API_TOKEN en .env)."
        )
        return
    try:
        data = football_api.get_competitions()
        comps = data.get("competitions", [])[:30]
        if not comps:
            await update.message.reply_text("No se encontraron competiciones.")
            return
        lines = ["📋 Ligas / competiciones\n", "─────────────────────"]
        for c in comps:
            code = c.get("code", "-")
            name = c.get("name", "?")
            area = (c.get("area") or {}).get("code")
            flag = _flag_for_area(area)
            lines.append(f"{flag} {code} — {name}")
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3970] + "\n… (truncado)"
        await update.message.reply_text(text)
    except FootballAPIError as e:
        await update.message.reply_text(f"Error API: {e}")


async def cmd_tabla(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tabla de posiciones: /tabla PL."""
    if not football_api:
        await update.message.reply_text("API de fútbol no configurada.")
        return
    if not context.args:
        await update.message.reply_text("Uso: /tabla (código). Ej: /tabla PL, /tabla PD")
        return
    code = context.args[0].upper()
    try:
        data = football_api.get_standings(code)
        standings = data.get("standings", [])
        if not standings:
            await update.message.reply_text(f"No hay tabla para {code}.")
            return
        table = standings[0].get("table", [])
        comp = data.get("competition", {})
        comp_name = comp.get("name", code)
        area_code = (comp.get("area") or {}).get("code")
        flag = _flag_for_area(area_code)
        lines = [
            f"📊 {flag} {_escape_html(comp_name)}",
            "─────────────────────",
            f"{'#':<3} {'Equipo':<22} {'Pts':>4} {'PJ':>3} {'V':>2} {'E':>2} {'D':>2} {'GF':>3} {'GC':>3}",
            "─────────────────────",
        ]
        for row in table[:20]:
            pos = row.get("position", "?")
            team_data = row.get("team", {})
            team_name = (team_data.get("shortName") or team_data.get("name") or "?")[:22]
            safe_name = _escape_html(team_name)
            # Padding para que columnas queden alineadas (el HTML se muestra con ancho visual del nombre original)
            safe_name = safe_name + " " * max(0, 22 - len(team_name))
            pts = row.get("points", 0)
            pj = row.get("playedGames", 0)
            pg = row.get("won", 0)
            pe = row.get("draw", 0)
            pp = row.get("lost", 0)
            gf = row.get("goalsFor", 0)
            gc = row.get("goalsAgainst", 0)
            lines.append(f"{pos:<3} {safe_name} {pts:>4} {pj:>3} {pg:>2} {pe:>2} {pp:>2} {gf:>3} {gc:>3}")
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3970] + "\n…"
        await update.message.reply_text(f"<pre>{text}</pre>", parse_mode="HTML")
    except FootballAPIError as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_partidos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Partidos de hoy o de una competición: /partidos [código]."""
    if not football_api:
        await update.message.reply_text("API de fútbol no configurada.")
        return
    try:
        if context.args:
            code = context.args[0].upper()
            data = football_api.get_matches_competition(
                code, status="SCHEDULED,FINISHED,IN_PLAY,LIVE", limit=25
            )
            comp = data.get("competition", {})
            comp_name = comp.get("name", code)
            flag = _flag_for_area((comp.get("area") or {}).get("code"))
            title = f"⚽ {flag} Partidos — {comp_name}\n"
        else:
            data = football_api.get_matches_today()
            title = "⚽ Partidos de hoy\n"
        matches = data.get("matches", [])
        if not matches:
            await update.message.reply_text(title + "No hay partidos.")
            return
        # Refrescar partidos que ya deberían haber terminado (la API a veces no actualiza a FINISHED)
        if context.args:
            matches = _refresh_stale_matches(football_api, matches)
        lines = [title, "─────────────────────────────"]
        for m in matches:
            mid = m.get("id")
            ht = m.get("homeTeam", {}).get("name", "?")
            at = m.get("awayTeam", {}).get("name", "?")
            score = m.get("score", {}).get("fullTime")
            status = m.get("status", "")
            comp_area = (m.get("competition") or {}).get("area") or {}
            match_flag = _flag_for_area(comp_area.get("code"))
            if score and score.get("home") is not None:
                res = f"{score.get('home')} - {score.get('away')}"
            else:
                res = "vs"
            date = m.get("utcDate", "")[:16].replace("T", " ")
            live_tag = " 🔴 EN VIVO " + _live_minute_text(m) if _is_reliably_live(m) else ""
            status_label = status
            if _match_should_have_ended(m) and (status or "").upper() not in ("FINISHED",):
                status_label = f"{status} (pend. actualizar)"
            lines.append(f"{match_flag} {ht}  {res}  {at}{live_tag}")
            lines.append(f"   📅 {date}  •  ID: {mid}  •  {status_label}")
            lines.append("")
        text = "\n".join(lines).strip()
        if len(text) > 4000:
            text = text[:3970] + "\n…"
        text += "\n\n💡 La API gratuita puede actualizar resultados con retraso. Vuelve a enviar para refrescar."
        await update.message.reply_text(text)
    except FootballAPIError as e:
        await update.message.reply_text(f"Error: {e}")


def _build_live_match_line(m: dict) -> str | None:
    """Construye la línea de un partido en vivo. Devuelve None si no está en vivo o los datos están obsoletos."""
    if not _is_reliably_live(m):
        return None
    ht = m.get("homeTeam", {}).get("name", "?")
    at = m.get("awayTeam", {}).get("name", "?")
    score = m.get("score", {}).get("fullTime") or {}
    comp = (m.get("competition") or {}).get("name", "?")
    comp_area = (m.get("competition") or {}).get("area") or {}
    flag = _flag_for_area(comp_area.get("code"))
    mid = m.get("id")
    hg = score.get("home")
    ag = score.get("away")
    if hg is not None and ag is not None:
        res = f"{hg} - {ag}"
    else:
        res = "0 - 0"
    min_text = _live_minute_text(m)
    return f"{flag} {ht}  {res}  {at}  ⏱ {min_text}\n   {comp}  •  ID: {mid}"


async def cmd_vivo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Partidos en vivo ahora: resultados actualizados al momento."""
    if not football_api:
        await update.message.reply_text("API de fútbol no configurada.")
        return
    try:
        data = football_api.get_matches_live()
        raw_matches = data.get("matches", [])
        lines = [
            "🔴 Partidos en vivo",
            "─────────────────────",
            "Vuelve a enviar /vivo para actualizar el marcador.",
            "💡 Si el marcador no cambia, la API gratuita puede tener retraso.",
            "",
        ]
        # Comprobar cada partido con el endpoint de detalle: el listado puede traer ya finalizados
        max_verify = 12  # límite para no pasarnos del rate limit
        for m in raw_matches[:max_verify]:
            mid = m.get("id")
            if not mid:
                continue
            try:
                full = football_api.get_match(mid)
            except FootballAPIError:
                continue
            line = _build_live_match_line(full)
            if line:
                lines.append(line)
                lines.append("")
        if len(lines) <= 4:
            await update.message.reply_text(
                "🔴 Partidos en vivo\n"
                "─────────────────────\n\n"
                "Ahora mismo no hay partidos en juego.\n\n"
                "Usa /partidos para ver partidos de hoy o de una liga."
            )
            return
        text = "\n".join(lines).strip()
        if len(text) > 4000:
            text = text[:3970] + "\n…"
        await update.message.reply_text(text)
    except FootballAPIError as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_partido(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Detalle de un partido: /partido <id>."""
    if not football_api:
        await update.message.reply_text("API de fútbol no configurada.")
        return
    if not context.args:
        await update.message.reply_text("Uso: /partido (id del partido)")
        return
    try:
        match_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("El id debe ser un número.")
        return
    try:
        m = football_api.get_match(match_id)
        home_team = m.get("homeTeam", {})
        away_team = m.get("awayTeam", {})
        ht_name = home_team.get("name", "?")
        at_name = away_team.get("name", "?")
        home_id = home_team.get("id")
        away_id = away_team.get("id")
        flag_h = _get_team_flag(football_api, home_id) if home_id else "🏳️"
        flag_a = _get_team_flag(football_api, away_id) if away_id else "🏳️"
        score = m.get("score", {}).get("fullTime") or {}
        comp_name = m.get("competition", {}).get("name", "?")
        comp_area = (m.get("competition") or {}).get("area") or {}
        comp_flag = _flag_for_area(comp_area.get("code"))
        date = m.get("utcDate", "")[:19].replace("T", " ")
        status = m.get("status", "?")

        hg = score.get("home")
        ag = score.get("away")
        if hg is not None and ag is not None:
            res = f"   {hg}  —  {ag}"
        else:
            res = "   vs"

        live_line = ""
        if _is_reliably_live(m):
            live_line = f"🔴 EN VIVO  •  {_live_minute_text(m)}\n"
        status_line = f"📌 Estado: {status}\n"
        refresh_tip = f"💡 Vuelve a usar /partido {match_id} para refrescar el marcador.\n" if _is_reliably_live(m) else ""
        pred_tip = f"💡 /prediccion {match_id} — Predicción estadística"

        score_display = f"{hg} - {ag}" if (hg is not None and ag is not None) else "vs"
        match_line = f"{flag_h} {ht_name}  •  {score_display}  •  {flag_a} {at_name}"

        caption = (
            f"⚽ Partido\n"
            f"─────────────────────\n"
            f"{live_line}"
            f"{match_line}\n"
            f"─────────────────────\n"
            f"🏆 {comp_flag} {comp_name}\n"
            f"📅 {date}\n"
            f"{status_line}"
            f"🆔 ID: {match_id}\n\n"
            f"{refresh_tip}{pred_tip}"
        )
        crest_h = home_team.get("crest")
        crest_a = away_team.get("crest")
        if len(caption) > 1024:
            caption = caption[:1021] + "…"
        try:
            photo_bytes = build_match_image(
                crest_h, crest_a, ht_name, at_name, score_display
            )
            await update.message.reply_photo(photo=photo_bytes, caption=caption)
        except Exception as e:
            logger.warning("No se pudo generar imagen con escudos: %s", e)
            await update.message.reply_text(caption)
    except FootballAPIError as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_prediccion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Predicción del ganador según estadísticas: /prediccion <id>."""
    if not football_api:
        await update.message.reply_text("API de fútbol no configurada.")
        return
    if not context.args:
        await update.message.reply_text("Uso: /prediccion (id del partido)")
        return
    try:
        match_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("El id debe ser un número.")
        return

    result = predict_match(football_api, match_id)
    if "error" in result:
        await update.message.reply_text(f"Error: {result['error']}")
        return

    home = result["home_team"]
    away = result["away_team"]
    # Obtener match para banderas y escudos
    try:
        m = football_api.get_match(match_id)
        hid = m.get("homeTeam", {}).get("id")
        aid = m.get("awayTeam", {}).get("id")
        flag_h = _get_team_flag(football_api, hid) if hid else "🏳️"
        flag_a = _get_team_flag(football_api, aid) if aid else "🏳️"
        crest_h = m.get("homeTeam", {}).get("crest")
        crest_a = m.get("awayTeam", {}).get("crest")
    except FootballAPIError:
        flag_h = flag_a = "🏳️"
        crest_h = crest_a = None

    conf = result.get("confidence", 0)
    h_pct = result.get("home_win_pct", 0)
    d_pct = result.get("draw_pct", 0)
    a_pct = result.get("away_win_pct", 0)
    reasons = result.get("reasons", [])
    summary = result.get("summary", "")

    match_line = f"{flag_h} {home}  vs  {flag_a} {away}"
    caption = (
        f"📈 Predicción estadística\n"
        f"─────────────────────\n"
        f"{match_line}\n"
        f"─────────────────────\n"
        f"🏆 {summary}\n"
        f"📊 Confianza: {conf}%\n\n"
        f"Probabilidades aproximadas:\n"
        f"  🏠 Local:     {h_pct}%\n"
        f"  🤝 Empate:    {d_pct}%\n"
        f"  ✈️ Visitante: {a_pct}%\n"
    )
    if reasons:
        caption += "\n─────────────────────\nCriterios:\n" + "\n".join(f"• {r}" for r in reasons)
    if len(caption) > 1024:
        caption = caption[:1021] + "…"

    try:
        photo_bytes = build_match_image(crest_h, crest_a, home, away, "vs")
        await update.message.reply_photo(photo=photo_bytes, caption=caption)
    except Exception as e:
        logger.warning("No se pudo generar imagen con escudos: %s", e)
        await update.message.reply_text(caption)


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        print("Configura TELEGRAM_BOT_TOKEN en .env")
        return
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ligas", cmd_ligas))
    app.add_handler(CommandHandler("tabla", cmd_tabla))
    app.add_handler(CommandHandler("partidos", cmd_partidos))
    app.add_handler(CommandHandler("vivo", cmd_vivo))
    app.add_handler(CommandHandler("partido", cmd_partido))
    app.add_handler(CommandHandler("prediccion", cmd_prediccion))
    logger.info("Bot iniciado. Ctrl+C para detener.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
