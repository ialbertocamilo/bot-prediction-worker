"""
Backfill 8 nuevas ligas con ventana histórica de 540 días.

- Itera las 8 ligas nuevas secuencialmente.
- Usa days_back=540 para alimentar la matriz de Poisson.
- El rate limiter (2s min entre requests) se respeta automáticamente
  por el ESPN scraper (app/providers/rate_limiter.py).
- El ingest es idempotente: upsert por external_id + signature,
  NO duplica partidos existentes de la primera carga (180 días).
- Si una liga falla, logea el error y continúa con la siguiente.

Uso:
    python -m scripts.backfill_new_leagues          # background recomendado
"""
import logging
import sys
import time

sys.path.insert(0, ".")

from app.db.session import SessionLocal
from app.services.canonical_league_service import CanonicalLeagueService

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Las 8 ligas añadidas en la expansión ──────────────────────────────────
NEW_LEAGUE_KEYS: list[str] = [
    "primeira-liga",
    "eredivisie",
    "brasileirao",
    "liga-profesional-arg",
    "liga-mx",
    "championship",
    "primera-a-colombia",
    "primera-division-chile",
]

DAYS_BACK = 540      # ~18 meses de historia para Dixon-Coles
DAYS_AHEAD = 30


def main() -> None:
    t0 = time.time()
    total_matches = 0
    errors: list[str] = []

    db = SessionLocal()
    try:
        svc = CanonicalLeagueService(db)

        for i, key in enumerate(NEW_LEAGUE_KEYS, 1):
            logger.info("=== [%d/%d] Backfill %s (days_back=%d) ===",
                        i, len(NEW_LEAGUE_KEYS), key, DAYS_BACK)
            league_t0 = time.time()
            try:
                n = svc.ingest_league(
                    key,
                    days_back=DAYS_BACK,
                    days_ahead=DAYS_AHEAD,
                )
                elapsed = time.time() - league_t0
                total_matches += n
                logger.info("OK: %s → %d matches (%.0fs)", key, n, elapsed)
            except Exception:
                elapsed = time.time() - league_t0
                logger.exception("FAIL: %s after %.0fs", key, elapsed)
                errors.append(key)

        total_elapsed = time.time() - t0
        logger.info("=== BACKFILL COMPLETO ===")
        logger.info("Total: %d matches en %.0f min", total_matches,
                     total_elapsed / 60)
        if errors:
            logger.warning("Ligas con error: %s", ", ".join(errors))
        else:
            logger.info("Sin errores.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
