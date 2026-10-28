"""Utilities for mapping provider statuses to a shared status model."""

NORMALISED_STATUS_MAP = {
    "submitted": "submitted",
    "sent": "sent",
    "delivered": "delivered",
    "read": "read",
    "failed": "failed",
    "undelivered": "undelivered",
    "blocked": "blocked",
    "expired": "expired",
}


def normalise_status(raw_status: str) -> str:
    return NORMALISED_STATUS_MAP.get(raw_status.lower(), raw_status.lower())
