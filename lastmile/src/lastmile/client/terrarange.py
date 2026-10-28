"""HTTP client for Terra Range fieldwork endpoints."""


class TerraRangeClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.token = token

    def health(self) -> dict:
        return {"status": "ok", "base_url": self.base_url}
