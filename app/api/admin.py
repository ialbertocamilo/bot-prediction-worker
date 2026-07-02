"""Admin endpoints — voucher generation (API-key protected)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.db.models.core.data_quality_report import DataQualityReport
from app.services.data_quality_service import DataQualityService
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


@router.post(
    "/data-quality-report/run",
    summary="Run data quality audit now",
)
def run_data_quality_report(
    _token: str = Depends(verify_admin_token),
    db: Session = Depends(get_db),
) -> dict:
    count = DataQualityService(db).run_and_persist()
    db.commit()
    return {"status": "ok", "issues_created": count}


@router.get(
    "/data-quality-report",
    summary="List recent data quality issues",
)
def list_data_quality_report(
    hours: int = 24,
    limit: int = 500,
    issue_type: str | None = None,
    _token: str = Depends(verify_admin_token),
    db: Session = Depends(get_db),
) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=max(1, min(hours, 24 * 30)))
    stmt = select(DataQualityReport).where(DataQualityReport.created_at >= since)
    if issue_type:
        stmt = stmt.where(DataQualityReport.issue_type == issue_type)
    stmt = stmt.order_by(DataQualityReport.created_at.desc(), DataQualityReport.id.desc()).limit(max(1, min(limit, 5000)))
    rows = list(db.scalars(stmt).all())
    return {
        "since": since.isoformat(),
        "count": len(rows),
        "items": [
            {
                "id": r.id,
                "issue_type": r.issue_type,
                "entity_id": r.entity_id,
                "description": r.description,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }
