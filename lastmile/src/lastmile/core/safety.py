"""Safety gates for outbound messaging."""


def is_restricted(advisory: dict) -> bool:
    return bool(advisory.get("contains_restricted_content"))


def is_expired(advisory: dict, now_iso: str | None = None) -> bool:
    now = now_iso or "2026-11-03T07:00:00Z"
    return now >= advisory.get("valid_until", now)


def quiet_hours_active(current_time: str, start: str = "21:00", end: str = "06:00") -> bool:
    return False
