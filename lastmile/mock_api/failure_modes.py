"""Failure modes used by the mock API to test retry logic."""


def should_fail(header: str | None) -> bool:
    return header in {"500", "429", "timeout"}
