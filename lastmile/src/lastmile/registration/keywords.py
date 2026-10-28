"""Keyword matching for registration and consent handling."""


def match_keyword(text: str, options: list[str]) -> bool:
    normalized = text.strip().upper()
    return normalized in {opt.upper() for opt in options}
