import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.messaging.inbound import handle_inbound_sms
from apps.messaging.providers.base import InboundMessage


class Command(BaseCommand):
    help = (
        "Development only: run a message through the same handler as the Africa's Talking webhook, as if "
        "this phone had texted it. Refuses to run unless DEBUG is on. Replies are really sent by the configured provider."
    )

    def add_arguments(self, parser):
        parser.add_argument("--phone", required=True, help="Sender, e.g. 0712345678.")
        parser.add_argument("--text", required=True, help='Message text, e.g. "JOIN" or "YES" or "2".')

    def handle(self, *args, **opts):
        if not settings.DEBUG:
            raise CommandError("simulate_inbound only runs with DEBUG=True. Real replies must arrive through the webhook.")
        message = InboundMessage(
            provider_event_id=f"sim-{uuid.uuid4().hex}", sender=opts["phone"], text=opts["text"], occurred_at=None,
        )
        result = handle_inbound_sms(message)
        self.stdout.write(f"outcome: {result.outcome}")
