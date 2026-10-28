"""Africa's Talking webhook helpers."""


def validate_token(token: str, expected: str) -> bool:
    return token == expected
