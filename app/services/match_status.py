"""Shared helpers for match status semantics."""
from __future__ import annotations

PREDICTABLE_FUTURE_MATCH_STATUSES: tuple[str, str] = ("SCHEDULED", "NS")


def is_predictable_future_match(status: str | None) -> bool:
    """Return True when a match status is a future state we can predict."""
    return status in PREDICTABLE_FUTURE_MATCH_STATUSES
