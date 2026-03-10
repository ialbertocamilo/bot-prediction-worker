from fastapi import APIRouter

from app.scheduler import get_scheduler_status

router = APIRouter()

@router.get("")
def health():
    return {"status": "ok", "scheduler": get_scheduler_status()}