"""Cliente para la API de football-data.org (v4). Datos de la temporada actual en plan gratuito.
Las peticiones usan cabeceras anti-caché para obtener siempre la última respuesta del servidor.
En plan gratuito, football-data.org puede actualizar resultados con cierto retraso."""
import requests
from datetime import datetime
from config import FOOTBALL_API_TOKEN, FOOTBALL_API_BASE


class FootballAPIError(Exception):
    """Error al llamar a la API de fútbol."""
    pass


class FootballAPI:
    """Cliente para obtener datos de partidos y estadísticas (temporada actual)."""

    def __init__(self, token: str | None = None):
        self.token = token or FOOTBALL_API_TOKEN
        self.session = requests.Session()
        self.session.headers["X-Auth-Token"] = self.token
        # Evitar que proxies/caché devuelvan datos antiguos
        self.session.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        self.session.headers["Pragma"] = "no-cache"

    def _get(self, path: str, params: dict | None = None) -> dict:
        r = self._request(path, params=params)
        return r.json()

    def _request(self, path: str, params: dict | None = None):
        """Realiza la petición GET y devuelve el objeto Response (para inspeccionar URL, headers, etc.)."""
        url = f"{FOOTBALL_API_BASE}{path}"
        r = self.session.get(url, params=params, timeout=15)
        if r.status_code == 429:
            raise FootballAPIError(
                "Límite de solicitudes alcanzado. Prueba en unos minutos."
            )
        if r.status_code != 200:
            raise FootballAPIError(f"API error {r.status_code}: {r.text[:200]}")
        return r

    def get_competitions(self):
        """Lista de competiciones disponibles."""
        return self._get("/competitions")

    def get_standings(self, competition_code: str):
        """Tabla de posiciones de una competición (ej: PL, PD, SA, CL)."""
        return self._get(f"/competitions/{competition_code}/standings")

    def get_matches_competition(
        self,
        competition_code: str,
        date_from: str | None = None,
        date_to: str | None = None,
        status: str = "SCHEDULED,FINISHED",
        limit: int = 20,
    ):
        """Partidos de una competición. status: SCHEDULED, FINISHED, etc."""
        params = {"limit": limit}
        if date_from:
            params["dateFrom"] = date_from
        if date_to:
            params["dateTo"] = date_to
        if status:
            params["status"] = status
        return self._get(f"/competitions/{competition_code}/matches", params=params)

    def get_matches_today(self, competition_ids: str | None = None):
        """Partidos de hoy (por defecto hoy en UTC)."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        params = {"dateFrom": today, "dateTo": today}
        if competition_ids:
            params["competitions"] = competition_ids
        return self._get("/matches", params=params)

    def get_matches_live(self, limit: int = 30):
        """Partidos en vivo ahora (IN_PLAY + PAUSED). El marcador se actualiza en cada petición."""
        return self._get("/matches", params={"status": "LIVE", "limit": limit})

    def get_match(self, match_id: int):
        """Detalle de un partido."""
        return self._get(f"/matches/{match_id}")

    def get_head2head(self, match_id: int, limit: int = 10):
        """Enfrentamientos previos entre los equipos del partido."""
        return self._get(f"/matches/{match_id}/head2head", params={"limit": limit})

    def get_team(self, team_id: int):
        """Detalle de un equipo (incluye área/país para bandera)."""
        return self._get(f"/teams/{team_id}")

    def get_team_matches(
        self,
        team_id: int,
        status: str = "FINISHED",
        limit: int = 10,
        date_from: str | None = None,
        date_to: str | None = None,
    ):
        """Últimos partidos de un equipo."""
        params = {"status": status, "limit": limit}
        if date_from:
            params["dateFrom"] = date_from
        if date_to:
            params["dateTo"] = date_to
        return self._get(f"/teams/{team_id}/matches", params=params)


def verify_response_freshness(competition_code: str = "CL", limit: int = 5) -> None:
    """
    Verifica si la API devuelve información actual: muestra URL de la petición,
    cabeceras de respuesta y lastUpdated del primer partido.
    Ejecutar: python football_api.py --verify
    """
    api = FootballAPI()
    if not api.token:
        print("Configura FOOTBALL_API_TOKEN en .env")
        return
    path = f"/competitions/{competition_code}/matches"
    params = {"limit": limit, "status": "SCHEDULED,FINISHED,IN_PLAY,LIVE"}
    print("--- Verificación de datos actuales ---")
    print(f"Hora local (ahora): {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    r = api._request(path, params=params)
    print(f"URL solicitada: {r.url}")
    print("\nCabeceras de respuesta (servidor):")
    for name in ("Date", "Cache-Control", "Age", "Last-Modified", "Expires"):
        if name in r.headers:
            print(f"  {name}: {r.headers[name]}")
    if not r.headers.get("Date"):
        print("  (no hay Date en respuesta)")
    data = r.json()
    matches = data.get("matches", [])
    if matches:
        m = matches[0]
        print("\nPrimer partido devuelto por la API:")
        print(f"  {m.get('homeTeam', {}).get('name')} vs {m.get('awayTeam', {}).get('name')}")
        print(f"  utcDate:    {m.get('utcDate')}")
        print(f"  status:     {m.get('status')}")
        print(f"  lastUpdated: {m.get('lastUpdated')}")
        score = m.get("score", {}).get("fullTime") or {}
        print(f"  score:      {score.get('home')}-{score.get('away')}")
        print(
            "\nConclusión: La petición llega al servidor ahora (cabecera Date ≈ ahora). "
            "Si 'lastUpdated' del partido es muy anterior a la hora actual, los datos del partido "
            "no se actualizan en tiempo real por parte de la API (limitación del plan/servidor)."
        )
    else:
        print("\nNo hay partidos en la respuesta.")
    print("--- Fin verificación ---")


def main():
    """Prueba rápida (requiere FOOTBALL_API_TOKEN)."""
    import sys
    if "--verify" in sys.argv:
        verify_response_freshness()
        return
    api = FootballAPI()
    if not api.token:
        print("Configura FOOTBALL_API_TOKEN en .env")
        return
    comps = api.get_competitions()
    print("Competiciones (sample):", [c["code"] for c in comps.get("competitions", [])[:5]])
    pl = api.get_standings("PL")
    print("Premier League standings:", pl.get("standings", [])[0].get("table", [])[:3])


if __name__ == "__main__":
    main()
