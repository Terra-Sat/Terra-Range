"""Assisted registration (officer or admin adds a person). Self-registration happens via the JOIN keyword."""
from django.utils import timezone

from apps.messaging.consent import apply_intent
from apps.messaging.core.keywords import Intent
from apps.messaging.core.phone import normalise_ke
from apps.messaging.enums import Channel, ConsentStatus, Purpose, Source
from apps.messaging.models import Consent, Location, Recipient
from apps.messaging.sending import send_transactional


class InvalidPhone(ValueError):
    """Raised for an invalid number. The message never contains the number itself."""


def find_location(code):
    """Look a location up by its code (exact first, then case-insensitive). None if unknown or blank."""
    code = (code or "").strip()
    if not code:
        return None
    return Location.objects.filter(code=code).first() or Location.objects.filter(code__iexact=code).first()


def register_recipient(*, phone, name="", ward_code="", role="", language="en", source=Source.ASSISTED, send_prompt=True):
    """Validate the phone, create the recipient, and ask for consent by SMS.

    ``ward_code`` is a Location code at any level. When it matches a Location the recipient is linked to it
    and, after replying YES, is not asked where they are. Otherwise they are asked over SMS.
    Registration alone never grants consent: the person must reply YES.
    Someone who has opted out (STOP) is never re-subscribed here; only they can text JOIN again.
    Returns (recipient, created).
    """
    normalised = normalise_ke(phone)
    if normalised is None:
        raise InvalidPhone("invalid_phone")
    location = find_location(ward_code)
    recipient, created = Recipient.objects.get_or_create(
        phone=normalised,
        defaults={
            "name": name, "ward_code": location.code if location else ward_code, "location": location,
            "role": role, "language": language, "source": source,
        },
    )
    if not created and location is not None and recipient.location_id is None:
        recipient.location, recipient.ward_code = location, location.code
        recipient.save(update_fields=["location", "ward_code"])
    consent = Consent.objects.filter(recipient=recipient, channel=Channel.SMS).first()
    status = consent.status if consent else ConsentStatus.NONE
    if status in (ConsentStatus.NONE, ConsentStatus.EXPIRED):
        result = apply_intent(recipient, Intent.JOIN, occurred_at=timezone.now(), source=source)
        if send_prompt and result.reply == Purpose.CONSENT_PROMPT:
            send_transactional(purpose=Purpose.CONSENT_PROMPT, phone=recipient.phone, recipient=recipient, language=recipient.language)
    return recipient, created
