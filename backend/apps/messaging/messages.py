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
