"""Configuración del bot desde variables de entorno."""
import os
from dotenv import load_dotenv

load_dotenv()

# Dixon-Coles time-decay factor (γ): weight = exp(-γ * days_since_match)
# 0.005 ≈ half-life ~139 days, consistent with DC literature
TIME_DECAY = float(os.getenv("TIME_DECAY", "0.005"))

# xG regularization weight for Dixon-Coles attack/defense priors.
# Higher values pull parameters more toward xG-implied strengths.
# 0.0 = no xG influence, 0.5 = gentle regularization.
XG_REG_WEIGHT = float(os.getenv("XG_REG_WEIGHT", "0.5"))

# Minimum xG-tracked matches a team needs before its xG prior is used.
# Prevents noisy priors from teams with very few xG data points.
MIN_XG_MATCHES = int(os.getenv("MIN_XG_MATCHES", "3"))

# Dixon-Coles home-advantage initial value.
# The optimizer starts from this value; bounded to [0.0, 1.5] during fitting.
HOME_ADVANTAGE = float(os.getenv("HOME_ADVANTAGE", "0.25"))

# Platt calibration toggle.  Set to "false" to disable post-hoc calibration.
CALIBRATION_ENABLED = os.getenv("CALIBRATION_ENABLED", "true").lower() in ("true", "1", "yes")

# Minimum evaluated predictions required before Platt calibration kicks in.
CALIBRATION_MIN_SAMPLES = int(os.getenv("CALIBRATION_MIN_SAMPLES", "50"))
