"""Meta WhatsApp webhook helpers."""

import hashlib
import hmac


def verify_meta_signature(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    expected = "sha256=" + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")
