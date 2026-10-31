"""Import an EXTERNAL seasonal outlook at ward level, one row per ward x lead x variable.

Inputs (choose one)
  --csv FILE   columns: ward_code, valid_start, valid_end, variable, value [, units]
  --tif FILE   a coarse GeoTIFF for ONE lead and ONE variable; needs --variable,
               --valid-start, --valid-end. Ward value = unweighted mean of the grid
               cells the ward touches (see pixel_store.zonal_mean_all_touched).

Lead convention (project choice, flagged): lead 1 is the calendar month of the
issue date, 2 the next month, 3 the one after. Override with --lead when a
provider counts differently. Rows beyond --max-lead (default 3) are SKIPPED and
reported, never silently dropped.

LIMITATIONS
  * File formats of specific providers (CHC multi-model maps, ICPAC GHACOF) have
    NOT been verified. Convert to the CSV or GeoTIFF shape above first.
  * Values are stored as supplied. Status is always Reported. Nothing here turns
    an outlook into a vegetation or VCI forecast.
  * Grids coarser than wards give neighbouring wards the same value. That is the
    forecast's resolution, not extra information.
"""
import csv
import datetime as dt
import re

from django.core.management.base import BaseCommand, CommandError

from ... import pixel_store as ps
from ...models import SeasonalOutlook

SLUG = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def lead_for(issued, valid_start):
    return (valid_start.year - issued.year) * 12 + valid_start.month - issued.month + 1


class Command(BaseCommand):
    help = "Import an external seasonal outlook at ward level (Reported)."

    def add_arguments(self, p):
        p.add_argument("--issued", required=True)
        p.add_argument("--source", required=True)
        p.add_argument("--methodology-version", default="outlook-0.1")
        p.add_argument("--csv")
        p.add_argument("--tif")
        p.add_argument("--variable")
        p.add_argument("--units", default="")
        p.add_argument("--valid-start")
        p.add_argument("--valid-end")
        p.add_argument("--band", type=int, default=1)
        p.add_argument("--lead", type=int)
        p.add_argument("--max-lead", type=int, default=3)
        p.add_argument("--ward-code-col")
        p.add_argument("--ward-table", default="public.wards")

    def handle(self, *a, **o):
        if bool(o["csv"]) == bool(o["tif"]):
            raise CommandError("give exactly one of --csv or --tif")
        issued = dt.date.fromisoformat(o["issued"])
        rows = []   # (ward, lead, vstart, vend, variable, units, value)
        if o["csv"]:
            with open(o["csv"], newline="") as f:
                for r in csv.DictReader(f):
                    vs = dt.date.fromisoformat(r["valid_start"])
                    ve = dt.date.fromisoformat(r["valid_end"])
                    val = r.get("value", "").strip()
                    rows.append((r["ward_code"].strip(), o["lead"] or lead_for(issued, vs),
                                 vs, ve, r["variable"].strip(), (r.get("units") or o["units"]).strip(),
                                 float(val) if val not in ("", "NA", "nan") else None))
        else:
            for need in ("variable", "valid_start", "valid_end", "ward_code_col"):
                if not o[need]:
                    raise CommandError(f"--tif needs --{need.replace('_', '-')}")
            vs = dt.date.fromisoformat(o["valid_start"])
            ve = dt.date.fromisoformat(o["valid_end"])
            geoms = ps.load_ward_geoms(o["ward_code_col"], o["ward_table"])
            z = ps.zonal_mean_all_touched(o["tif"], geoms, band=o["band"])
            lead = o["lead"] or lead_for(issued, vs)
            one_cell = [c for c, (_, n) in z.items() if n <= 1]
            for code, (val, _) in z.items():
                rows.append((code, lead, vs, ve, o["variable"], o["units"], val))
            if one_cell:
                self.stdout.write(self.style.WARNING(
                    f"{len(one_cell)} wards touch at most one grid cell, so their value is "
                    "that cell's value (forecast resolution, not local detail)"))

        bad = [r[4] for r in rows if not SLUG.match(r[4])]
        if bad:
            raise CommandError(f"variable names must be lowercase slugs, got {sorted(set(bad))}")
        keep = [r for r in rows if 1 <= r[1] <= o["max_lead"]]
        skipped = len(rows) - len(keep)
        n_null = 0
        for ward, lead, vs, ve, var, units, val in keep:
            SeasonalOutlook.objects.update_or_create(
                issued=issued, lead=lead, ward_code=ward, variable=var, source=o["source"],
                defaults=dict(valid_start=vs, valid_end=ve, units=units, value=val,
                              methodology_version=o["methodology_version"], status="Reported"))
            n_null += val is None
        self.stdout.write(self.style.SUCCESS(
            f"imported {len(keep)} rows ({n_null} NULL = missing)"))
        if skipped:
            self.stdout.write(self.style.WARNING(
                f"skipped {skipped} rows outside lead 1..{o['max_lead']}"))
