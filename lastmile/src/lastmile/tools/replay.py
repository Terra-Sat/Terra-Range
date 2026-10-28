"""Replay webhooks and provider responses."""


def replay(path: str) -> dict:
    return {"source": path, "status": "replayed"}
