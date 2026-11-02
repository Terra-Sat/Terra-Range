"""Phone number validation and normalisation (Kenya, E.164)."""
import re

KE_E164 = re.compile(r"^\+254[17]\d{8}$")
_ALLOWED_CHARS = re.compile(r"^\+?[\d\s\-().]+$")


def normalise_ke(raw):
    """Return a Kenyan mobile number as +254XXXXXXXXX, or None if invalid."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not _ALLOWED_CHARS.match(s):
        return None
    digits = re.sub(r"\D", "", s)
    if s.startswith("+"):
        if not digits.startswith("254"):
            return None
        candidate = "+" + digits
    elif digits.startswith("254"):
        candidate = "+" + digits
    elif digits.startswith("0") and len(digits) == 10:
        candidate = "+254" + digits[1:]
    elif len(digits) == 9 and digits[0] in "71":
        candidate = "+254" + digits
    else:
        return None
    return candidate if KE_E164.match(candidate) else None
