"""Consent state logic."""


def consent_active(consent: dict) -> bool:
    return consent.get("status") == "active"
