"""Configuración del bot desde variables de entorno."""
import os
from dotenv import load_dotenv

load_dotenv()

# Dixon-Coles time-decay factor (γ): weight = exp(-γ * days_since_match)
# 0.005 ≈ half-life ~139 days, consistent with DC literature
TIME_DECAY = float(os.getenv("TIME_DECAY", "0.005"))

# xG regularization weight for Dixon-Coles attack/defense priors.
# Higher values pull parameters more toward xG-implied strengths.
# 0.0 = no xG influence, 3.0 = moderate regularization.
XG_REG_WEIGHT = float(os.getenv("XG_REG_WEIGHT", "3.0"))
