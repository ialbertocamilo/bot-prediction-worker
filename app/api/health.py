import socket
import urllib.request
import urllib.error
import json

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.football.match import Match
from app.db.models.football.match_stats import MatchStats
from app.api.dependencies import get_db
from app.providers.cache import get_provider_cache
from app.providers.rate_limiter import get_all_metrics as get_rate_limiter_metrics
from app.scheduler import get_scheduler_status

router = APIRouter()

_DOCKER_SOCKET = "/var/run/docker.sock"
_SERVICE_NAMES = ("bot", "worker")


def _docker_request(path: str) -> dict | list | None:
    try:
        conn = _UnixSocketHTTPConnection(_DOCKER_SOCKET)
        conn.request("GET", path, headers={"Host": "localhost"})
        resp = conn.getresponse()
        return json.loads(resp.read().decode())
    except Exception:
        return None


class _UnixSocketHTTPConnection:
    def __init__(self, socket_path: str):
        import http.client
        self._path = socket_path
        self._conn = http.client.HTTPConnection("localhost")
        self._conn.sock = self._make_sock()

    def _make_sock(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self._path)
        return s

    def request(self, method: str, url: str, headers: dict):
        self._conn.request(method, url, headers=headers)

    def getresponse(self):
        return self._conn.getresponse()


def _get_service_status(name: str) -> dict:
    containers: list | None = _docker_request(
        f"/containers/json?all=1&filters=%7B%22name%22%3A%5B%22{name}%22%5D%7D"
    )
    if containers is None:
        return {"status": "unknown", "detail": "docker socket unavailable"}
    if not containers:
        return {"status": "not_found", "detail": "no container matched"}

    c = containers[0]
    state = c.get("State", "unknown")
    return {
        "status": "up" if state == "running" else "down",
        "state": state,
        "name": c.get("Names", [name])[0].lstrip("/"),
        "image": c.get("Image", ""),
        "started_at": c.get("Status", ""),
    }


@router.get("")
def health():
    return {"status": "ok", "scheduler": get_scheduler_status()}


@router.get("/services")
def services_health():
    results = {name: _get_service_status(name) for name in _SERVICE_NAMES}
    overall = "ok" if all(s["status"] == "up" for s in results.values()) else "degraded"
    return {"status": overall, "services": results}


@router.get("/metrics")
def metrics(db: Session = Depends(get_db)):
    """Observability endpoint: provider metrics, cache, stats coverage."""
    # Stats coverage
    total_finished = db.scalar(
        select(func.count(Match.id)).where(Match.status == "FINISHED")
    ) or 0
    matches_with_stats = db.scalar(
        select(func.count(func.distinct(MatchStats.match_id)))
    ) or 0
    total_scheduled = db.scalar(
        select(func.count(Match.id)).where(Match.status.in_(("SCHEDULED", "NS")))
    ) or 0

    coverage_pct = round(matches_with_stats / max(total_finished, 1) * 100, 1)

    return {
        "matches": {
            "finished": total_finished,
            "scheduled": total_scheduled,
            "with_stats": matches_with_stats,
            "stats_coverage_pct": coverage_pct,
        },
        "rate_limiters": get_rate_limiter_metrics(),
        "provider_cache": get_provider_cache().get_metrics(),
        "scheduler": get_scheduler_status(),
    }
