"""Helpers so personal data never reaches logs or error messages."""


def mask_phone(phone):
    """Hide everything except the last 3 digits, e.g. '**********678'."""
    if not phone or not isinstance(phone, str):
        return "***"
    visible = phone[-3:]
    return "*" * max(len(phone) - 3, 3) + visible
