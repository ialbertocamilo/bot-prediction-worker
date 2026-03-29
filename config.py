"""Configuración del bot desde variables de entorno."""
import os
from dotenv import load_dotenv

load_dotenv()

# Dixon-Coles time-decay factor (ξ): weight = exp(-ξ * days_since_match)
# 0.007 ≈ half-life ~99 days.  Optimized via grid search on EPL 2024-26 (LL=1.0037).
# Combined with 365-day rolling window for training data.
TIME_DECAY = float(os.getenv("TIME_DECAY", "0.007"))

# Rolling window size in days for training data.
# Only matches within this window before the target date are used.
TRAINING_WINDOW_DAYS = int(os.getenv("TRAINING_WINDOW_DAYS", "365"))

# xG regularization weight for Dixon-Coles attack/defense priors.
# Higher values pull parameters more toward xG-implied strengths.
# 7.5 = strong xG prior.  Optimized via grid search on EPL 2024-26 (LL=1.0037).
XG_REG_WEIGHT = float(os.getenv("XG_REG_WEIGHT", "7.5"))

# Minimum xG-tracked matches a team needs before its xG prior is used.
# Prevents noisy priors from teams with very few xG data points.
MIN_XG_MATCHES = int(os.getenv("MIN_XG_MATCHES", "3"))

# Dixon-Coles home-advantage value (fixed during fitting via home_adv_fixed=True).
# 0.18 = moderate home edge.  Optimized via grid search on EPL 2024-26 (LL=1.0037).
HOME_ADVANTAGE = float(os.getenv("HOME_ADVANTAGE", "0.18"))

# ── Kelly / Staking ──────────────────────────────────────────────────────
# Fractional Kelly multiplier.  0.10 = use only 10% of theoretical Kelly stake.
# Full Kelly is PROHIBITED — always use a fraction to limit ruin risk.
KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.10"))

# Hard cap: never recommend more than this % of bankroll on a single bet.
MAX_STAKE_PERCENT = float(os.getenv("MAX_STAKE_PERCENT", "0.05"))

# Platt calibration toggle.  Set to "false" to disable post-hoc calibration.
CALIBRATION_ENABLED = os.getenv("CALIBRATION_ENABLED", "true").lower() in ("true", "1", "yes")

# Minimum evaluated predictions required before Platt calibration kicks in.
# Lowered to 30 to allow early-season calibration (Jornada 10-12).
CALIBRATION_MIN_SAMPLES = int(os.getenv("CALIBRATION_MIN_SAMPLES", "30"))

# ── App Base URL (ngrok / dominio público) ───────────────────────────────
# Requerido por los providers de pago para notification_url y back_urls.
_raw_base_url = os.getenv("APP_BASE_URL", "").rstrip("/")
if not _raw_base_url:
    raise RuntimeError(
        "FATAL: APP_BASE_URL no configurada en .env — "
        "los webhooks de Mercado Pago y PayPal no funcionarán. "
        "Ejemplo: APP_BASE_URL=https://tu-subdominio.ngrok-free.dev"
    )
APP_BASE_URL: str = _raw_base_url
