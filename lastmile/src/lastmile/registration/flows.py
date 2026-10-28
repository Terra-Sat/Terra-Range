"""Registration flow orchestration."""


def join_flow(recipient: dict, channel: str) -> dict:
    return {"recipient_id": recipient.get("id"), "channel": channel, "status": "pending"}
