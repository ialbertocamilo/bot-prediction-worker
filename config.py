"""Configuración del bot desde variables de entorno."""
import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# football-data.org v4 — datos de la temporada actual en plan gratuito
FOOTBALL_API_TOKEN = os.getenv("FOOTBALL_API_TOKEN", "")
FOOTBALL_API_BASE = "https://api.football-data.org/v4"

# Dixon-Coles time-decay factor (γ): weight = exp(-γ * days_since_match)
# 0.005 ≈ half-life ~139 days, consistent with DC literature
TIME_DECAY = float(os.getenv("TIME_DECAY", "0.005"))
