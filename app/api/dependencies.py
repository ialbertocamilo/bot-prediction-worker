"""Shared FastAPI dependency factories for API routers."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.session import SessionLocal


def get_db():
    """Yield a transactional SQLAlchemy session.

    Commits on success, rolls back on exception, always closes.
    """
    db: Session = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
