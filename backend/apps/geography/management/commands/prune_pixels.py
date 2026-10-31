"""Delete stored pixel layers older than a date (class shares go with them).

  python manage.py prune_pixels --keep-from 2025-01-01            # dry run
  python manage.py prune_pixels --keep-from 2025-01-01 --yes      # delete
  python manage.py prune_pixels --keep-days 730 --yes             # rolling window

Dry run is the default. Seasonal outlooks are NEVER pruned here: they are tiny
and are the only record for checking later how past outlooks compared with
observed rainfall.

LIMITATION: Postgres does not return disk to the operating system after a
DELETE. The space is reused by new layers. Run VACUUM FULL on the two tables
only if you need the plan's disk figure itself to drop (it locks the tables).
"""
import datetime as dt

from django.core.management.base import BaseCommand, CommandError

from ...models import PixelLayer


class Command(BaseCommand):
    help = "Delete pixel layers older than --keep-from / --keep-days (dry run by default)."

    def add_arguments(self, p):
        p.add_argument("--indicator", default="vci")
        p.add_argument("--keep-from")
        p.add_argument("--keep-days", type=int)
        p.add_argument("--yes", action="store_true")

    def handle(self, *a, **o):
        if bool(o["keep_from"]) == bool(o["keep_days"]):
            raise CommandError("give exactly one of --keep-from or --keep-days")
        cutoff = (dt.date.fromisoformat(o["keep_from"]) if o["keep_from"]
                  else dt.date.today() - dt.timedelta(days=o["keep_days"]))
        qs = PixelLayer.objects.filter(indicator=o["indicator"], period_start__lt=cutoff)
        n = qs.count()
        kept = PixelLayer.objects.filter(indicator=o["indicator"],
                                         period_start__gte=cutoff).count()
        if n and kept == 0:
            raise CommandError("this would delete every layer; refusing. Check the date.")
        mb = sum(len(bytes(v)) for v in qs.values_list("values", flat=True)) / 1e6
        self.stdout.write(f"{n} layers before {cutoff} (about {mb:.0f} MB), {kept} kept")
        if not o["yes"]:
            self.stdout.write("dry run, nothing deleted. Add --yes to delete.")
            return
        qs.delete()
        self.stdout.write(self.style.SUCCESS(f"deleted {n} layers"))
