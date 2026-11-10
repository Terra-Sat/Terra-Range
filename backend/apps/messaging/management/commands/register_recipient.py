from django.core.management.base import BaseCommand, CommandError

from apps.messaging.core.redact import mask_phone
from apps.messaging.enums import Channel
from apps.messaging.models import Consent
from apps.messaging.registration import InvalidPhone, register_recipient


class Command(BaseCommand):
    help = "Register a recipient and send the SMS consent prompt. Consent is only granted when they reply YES."

    def add_arguments(self, parser):
        parser.add_argument("--phone", required=True)
        parser.add_argument("--name", default="")
        parser.add_argument("--ward", default="", help="Location code at any level (county, sub-county or ward). Leave out to let them choose over SMS.")
        parser.add_argument("--role", default="")
        parser.add_argument("--language", choices=["en", "sw"], default="en")
        parser.add_argument("--no-prompt", action="store_true", help="Register without sending the consent SMS.")

    def handle(self, *args, **opts):
        try:
            recipient, created = register_recipient(
                phone=opts["phone"], name=opts["name"], ward_code=opts["ward"], role=opts["role"],
                language=opts["language"], send_prompt=not opts["no_prompt"],
            )
        except InvalidPhone:
            raise CommandError("Invalid Kenyan mobile number.")
        consent = Consent.objects.filter(recipient=recipient, channel=Channel.SMS).first()
        self.stdout.write(
            f"{'Registered' if created else 'Already registered'} {recipient.id} "
            f"({mask_phone(recipient.phone)}); sms consent: {consent.status if consent else 'none'}"
        )
