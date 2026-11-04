from django.core.management.base import BaseCommand, CommandError

from apps.messaging import locations_loader as loader


class Command(BaseCommand):
    help = (
        "Load the location tree (county, sub-county, ward, or any depth) that people choose from over SMS. "
        "Safe to re-run: rows are matched by code."
    )

    def add_arguments(self, parser):
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--csv", help="CSV with columns code,name,level,parent_code[,sort_order].")
        source.add_argument("--wards-geojson", help="The project's wards.geojson (county, sub-county and ward fields).")
        parser.add_argument(
            "--ward-code-field", default="uid",
            help="geojson property used as the ward code. Must match the ward_code the data pipeline stores (default: uid).",
        )
        parser.add_argument("--dry-run", action="store_true", help="Validate and report, but change nothing.")
        parser.add_argument(
            "--deactivate-missing", action="store_true",
            help="Hide locations that are not in this file from the SMS questions (recipients keep their link).",
        )

    def handle(self, *args, **opts):
        try:
            if opts["csv"]:
                rows = loader.rows_from_csv(opts["csv"])
            else:
                rows = loader.rows_from_wards_geojson(opts["wards_geojson"], opts["ward_code_field"])
            summary = loader.load(rows, deactivate_missing=opts["deactivate_missing"], dry_run=opts["dry_run"])
        except FileNotFoundError as exc:
            raise CommandError(f"File not found: {exc.filename}")
        except loader.LoadError as exc:
            for problem in exc.errors:
                self.stderr.write(f"  - {problem}")
            raise CommandError(f"{len(exc.errors)} problem(s) found. Nothing was loaded.")
        prefix = "[dry run] " if opts["dry_run"] else ""
        self.stdout.write(self.style.SUCCESS(
            f"{prefix}{len(rows)} rows: {summary.created} created, {summary.updated} updated, "
            f"{summary.unchanged} unchanged, {summary.deactivated} deactivated."
        ))
        for warning in summary.warnings:
            self.stdout.write(self.style.WARNING(f"warning: {warning}"))
