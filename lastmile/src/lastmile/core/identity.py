import re

KE_E164 = re.compile(r"^\+254[17]\d{8}$")


def normalise_ke(raw: str) -> str | None:
    digits = re.sub(r"[^\d+]", "", raw or "")
    if digits.startswith("+254"):
        candidate = digits
    elif digits.startswith("254"):
        candidate = "+" + digits
    elif digits.startswith("0") and len(digits) == 10:
        candidate = "+254" + digits[1:]
    elif len(digits) == 9 and digits[0] in "71":
        candidate = "+254" + digits
    else:
        return None
    return candidate if KE_E164.match(candidate) else None
