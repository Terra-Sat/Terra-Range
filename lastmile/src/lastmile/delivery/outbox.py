"""Simple SQLite-backed outbox placeholder."""


def enqueue_message(message: dict) -> str:
    return message.get("idempotency_key", "job-1")
