from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.services.team_service import TeamService

router = APIRouter()


@router.get("/search")
def search_teams(
    q: str = Query(..., min_length=2, max_length=100, description="Team name search query"),
    db: Session = Depends(get_db),
) -> dict:
    svc = TeamService(db)
    results = svc.search(q)
    return {"results": results}


@router.get("/{team_id}/competitions")
def team_competitions(
    team_id: int,
    db: Session = Depends(get_db),
) -> dict:
    svc = TeamService(db)
    data = svc.active_competitions(team_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Team {team_id} not found")
    return data


@router.get("/{team_id}/matches")
def team_matches(
    team_id: int,
    status: str | None = Query(None, description="Filter by status: FINISHED, SCHEDULED, NS"),
    limit: int = Query(50, ge=1, le=200, description="Max matches to return"),
    db: Session = Depends(get_db),
) -> dict:
    svc = TeamService(db)
    data = svc.team_matches(team_id, status=status, limit=limit)
    if not data:
        raise HTTPException(status_code=404, detail=f"Team {team_id} not found")
    return data
