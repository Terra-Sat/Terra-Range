"""Fixed SMS texts. Keep them GSM-7 safe (plain ASCII) and short.

Swahili texts are drafts and need review by a native speaker.
"""

TEMPLATES = {
    "consent_prompt": {
        "en": "Terra Range: reply YES to get pasture and water alerts by SMS. Reply STOP anytime to opt out, HELP for help.",
        "sw": "Terra Range: jibu NDIYO kupokea tahadhari za malisho na maji kwa SMS. Jibu ACHA kujiondoa, MSAADA kwa usaidizi.",
    },
    "welcome": {
        "en": "Terra Range: you are subscribed to pasture and water alerts. Reply HELP for help or STOP to opt out.",
        "sw": "Terra Range: umejiandikisha kupokea tahadhari za malisho na maji. Jibu MSAADA kwa usaidizi au ACHA kujiondoa.",
    },
    "stop_confirmation": {
        "en": "Terra Range: you are unsubscribed and will get no more alerts. Reply JOIN to subscribe again.",
        "sw": "Terra Range: umejiondoa na hutapokea tahadhari zaidi. Jibu JIUNGE kujiandikisha tena.",
    },
    "help": {
        "en": "Terra Range sends pasture and water alerts. Reply JOIN to subscribe, YES to confirm, STOP to opt out.",
        "sw": "Terra Range hutuma tahadhari za malisho na maji. Jibu JIUNGE kujiandikisha, NDIYO kuthibitisha, ACHA kujiondoa.",
    },
}


def render(purpose, language="en"):
    options = TEMPLATES[str(purpose)]
    return options.get(language) or options["en"]


# --- Location conversation (dynamic text: the options come from the Location tree) ---

_LOCATION_TEXT = {
    "en": {
        "ask": "Terra Range: which {level} are you in? Reply with a number:",
        "more": "MORE for more options",
        "back": "BACK to go one step back",
        "unlisted": "0 if {name} is as close as you can say",
        "sorry": "Sorry, we did not understand that.",
        "saved": "Terra Range: thank you. You are registered for {label}. Reply STATUS for conditions, LOCATION to change it, STOP to opt out.",
    },
    "sw": {
        "ask": "Terra Range: uko {level} gani? Jibu kwa nambari:",
        "more": "ZAIDI kwa chaguo zaidi",
        "back": "NYUMA kurudi nyuma",
        "unlisted": "0 ikiwa {name} ndio eneo la karibu zaidi",
        "sorry": "Samahani, hatukuelewa.",
        "saved": "Terra Range: asante. Umesajiliwa kwa {label}. Jibu HALI kwa hali ya sasa, ENEO kubadilisha, ACHA kujiondoa.",
    },
}


def _text(language):
    return _LOCATION_TEXT.get(language) or _LOCATION_TEXT["en"]


def render_location_prompt(language, *, level, options, more=False, back=False, unlisted_name="", sorry=False):
    """The numbered question for one page of options. ``options`` is a list of display names."""
    t = _text(language)
    lines = [t["ask"].format(level=level.replace("_", " "))]
    lines += [f"{number}. {name}" for number, name in enumerate(options, start=1)]
    footer = []
    if more:
        footer.append(t["more"])
    if back:
        footer.append(t["back"])
    if unlisted_name:
        footer.append(t["unlisted"].format(name=unlisted_name))
    if footer:
        lines.append("Reply " + "; ".join(footer) + ".")
    body = "\n".join(lines)
    return f"{t['sorry']}\n{body}" if sorry else body


def render_location_saved(language, label):
    return _text(language)["saved"].format(label=label)
