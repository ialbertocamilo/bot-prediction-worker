"""Servicio de análisis de partidos en vivo con Gemini 1.5 Flash."""
from __future__ import annotations

import logging

from google import genai

from app.db.models.football.match import Match

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "Eres un analista experto en fútbol y apuestas deportivas en vivo. "
    "Te daré los datos de un partido que se está jugando AHORA MISMO. "
    "Tu trabajo es dar una predicción rápida, técnica y directa "
    "(máximo 3 párrafos cortos) sobre la tendencia del partido y qué "
    "podría pasar (próximo gol, resultado final). "
    "REGLA CRÍTICA: Basa tu análisis ÚNICAMENTE en el marcador y minuto "
    "actual. No inventes estadísticas (posesión, tarjetas) que no te he "
    "proporcionado."
)


async def analyze_live_match(match: Match, api_key: str) -> str:
    """Genera un análisis en vivo de un partido usando Gemini 1.5 Flash.

    Args:
        match: Instancia de Match con status IN_PLAY.
        api_key: Gemini API key.

    Returns:
        Texto del análisis generado por el modelo.

    Raises:
        ValueError: si la API key está vacía.
        RuntimeError: si Gemini no devuelve contenido.
    """
    if not api_key:
        raise ValueError("GEMINI_API_KEY no configurada")

    client = genai.Client(api_key=api_key)

    home = match.home_team.name if match.home_team else "Local"
    away = match.away_team.name if match.away_team else "Visitante"
    home_goals = match.home_goals if match.home_goals is not None else 0
    away_goals = match.away_goals if match.away_goals is not None else 0
    clock = match.clock_display or "desconocido"

    user_prompt = (
        f"Partido en vivo:\n"
        f"  {home} {home_goals} - {away_goals} {away}\n"
        f"  Minuto: {clock}\n\n"
        f"Dame tu análisis rápido de la tendencia y predicción."
    )

    response = await client.aio.models.generate_content(
        model="gemini-1.5-flash",
        contents=user_prompt,
        config=genai.types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
        ),
    )

    if not response.text:
        raise RuntimeError("Gemini no devolvió contenido para el análisis")

    return response.text
