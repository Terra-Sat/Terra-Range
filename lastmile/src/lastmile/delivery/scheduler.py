"""Scheduling helpers for delivery retries and backoff."""


def next_delay(attempt: int, base_seconds: int = 5) -> int:
    return base_seconds * (2 ** attempt)
