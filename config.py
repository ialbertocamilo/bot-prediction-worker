"""Configuración del bot desde variables de entorno."""
import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# football-data.org v4 — datos de la temporada actual en plan gratuito
FOOTBALL_API_TOKEN = os.getenv("FOOTBALL_API_TOKEN", "")
FOOTBALL_API_BASE = "https://api.football-data.org/v4"
