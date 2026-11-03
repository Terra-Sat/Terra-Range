"""Turn an inbound SMS text into an intent (JOIN, YES, STOP, HELP)."""
import re
from enum import Enum


class Intent(str, Enum):
    JOIN = "join"
    CONFIRM = "confirm"
    STOP = "stop"
    HELP = "help"
    STATUS = "status"
    UNKNOWN = "unknown"


# English plus Swahili placeholders (to be reviewed by native speakers).
KEYWORDS = {
    Intent.JOIN: {"JOIN", "JIUNGE"},
    Intent.CONFIRM: {"YES", "NDIYO"},
    Intent.STOP: {"STOP", "ACHA"},
    Intent.HELP: {"HELP", "MSAADA"},
    Intent.STATUS: {"STATUS", "HALI"},
}

_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)


def normalise_text(text):
    """Uppercase, replace punctuation with spaces, collapse whitespace."""
    if not isinstance(text, str):
        return ""
    return " ".join(_PUNCTUATION.sub(" ", text).upper().split())


def parse_intent(text):
    normalised = normalise_text(text)
    if not normalised:
        return Intent.UNKNOWN
    # Opt-out is deliberately forgiving: "STOP", "stop!", "STOP ALL", "stop please".
    if normalised.split()[0] in KEYWORDS[Intent.STOP]:
        return Intent.STOP
    for intent, words in KEYWORDS.items():
        if normalised in words:
            return intent
    return Intent.UNKNOWN
