"""Template rendering and language fallback."""


def render_template(advisory: dict, language: str, channel: str) -> str:
    content = advisory.get("content", {})
    preferred = content.get(language) or content.get("en") or ""
    return f"[{channel.upper()}] {preferred}"
