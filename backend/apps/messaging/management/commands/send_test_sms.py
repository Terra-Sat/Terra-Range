from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.messaging.core.phone import normalise_ke
from apps.messaging.core.redact import mask_phone
from apps.messaging.providers.base import SendRequest
from apps.messaging.providers.registry import get_sms_provider


class Command(BaseCommand):
    help = "Send one test SMS through the configured provider, to check your Africa's Talking key."

    def add_arguments(self, parser):
        parser.add_argument("--phone", required=True)
        parser.add_argument("--message", default="Terra Range test message")

    def handle(self, *args, **opts):
        phone = normalise_ke(opts["phone"])
        if phone is None:
            raise CommandError("Invalid Kenyan mobile number.")
        provider = get_sms_provider()
        result = provider.send(SendRequest(to=phone, body=opts["message"]))
        key = getattr(settings, "AT_API_KEY", "")
        self.stdout.write(
            f"provider={provider.name} base_url={getattr(settings, 'AT_BASE_URL', '-')} "
            f"username={getattr(settings, 'AT_USERNAME', '-')!r} key_length={len(key)} to={mask_phone(phone)}"
        )
        if result.accepted:
            self.stdout.write(self.style.SUCCESS(f"accepted, message id {result.provider_message_id or '(none)'}"))
        else:
            self.stdout.write(self.style.ERROR(f"not accepted: {result.error_code} (retryable={result.retryable})"))
            if result.detail:
                self.stdout.write(f"Africa's Talking said: {result.detail}")
            if result.error_code in ("http_401", "http_403", "not_configured"):
                self.stdout.write("Check AT_USERNAME, AT_API_KEY and AT_BASE_URL in your .env file.")
