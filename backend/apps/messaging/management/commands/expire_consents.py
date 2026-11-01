from django.core.management.base import BaseCommand

from apps.messaging.consent import expire_pending


class Command(BaseCommand):
    help = "Expire pending consents that were never confirmed within the configured time limit."

    def handle(self, *args, **opts):
        self.stdout.write(f"Expired {expire_pending()} pending consent(s).")
