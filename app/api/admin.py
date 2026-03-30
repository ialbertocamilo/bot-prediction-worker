"""Admin endpoints — voucher generation (API-key protected)."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.services.voucher_service import generate_voucher

# ── Security dependency ──────────────────────────────────────────────

_api_key_header = APIKeyHeader(name="X-Admin-Token", auto_error=False)

_ADMIN_SECRET: str = os.getenv("ADMIN_SECRET_KEY", "")


async def verify_admin_token(
    api_key: str | None = Security(_api_key_header),
) -> str:
    """Validate the ``X-Admin-Token`` header against the master key."""
    if not _ADMIN_SECRET:
        raise HTTPException(
            status_code=500,
            detail="ADMIN_SECRET_KEY not configured on server.",
        )
    if api_key is None or api_key != _ADMIN_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized.")
    return api_key


# ── Schemas ──────────────────────────────────────────────────────────

class VoucherCreateRequest(BaseModel):
    credits: int = Field(..., gt=0, description="Credits to load into the voucher")


class VoucherCreateResponse(BaseModel):
    status: str = "success"
    voucher_code: str
    credits: int


# ── Router ───────────────────────────────────────────────────────────

router = APIRouter(tags=["Admin"])


@router.post(
    "/vouchers/generate",
    response_model=VoucherCreateResponse,
    summary="Generate a credit voucher",
)
def create_voucher(
    body: VoucherCreateRequest,
    _token: str = Depends(verify_admin_token),
    db: Session = Depends(get_db),
) -> VoucherCreateResponse:
    code = generate_voucher(db, body.credits)
    return VoucherCreateResponse(
        voucher_code=code,
        credits=body.credits,
    )
