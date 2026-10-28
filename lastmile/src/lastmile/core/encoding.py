"""Helpers for SMS encoding and segment-count calculations."""

GSM7_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM7_EXT = set("^{}\\[~]|€\f")


def sms_segments(text: str) -> tuple[int, str]:
    if all(ch in GSM7_BASIC or ch in GSM7_EXT for ch in text):
        length = sum(2 if ch in GSM7_EXT else 1 for ch in text)
        enc, single, multi = "gsm7", 160, 153
    else:
        length = len(text.encode("utf-16-le")) // 2
        enc, single, multi = "ucs2", 70, 67

    if length <= single:
        return 1, enc
    return -(-length // multi), enc
