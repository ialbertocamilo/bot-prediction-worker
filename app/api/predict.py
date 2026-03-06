from fastapi import APIRouter

router = APIRouter()

@router.get("/{match_id}")
def predict(match_id: int):
    return {
        "match_id": match_id,
        "p_home": 0.45,
        "p_draw": 0.30,
        "p_away": 0.25,
        "model": "poisson_v0"
    }