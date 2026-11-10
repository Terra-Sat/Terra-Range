"""Import water points for the whole region, then tag each with its ward.

    python manage.py import_water --source osm
    python manage.py import_water --file data/raw/water/ken_waterpoints1.csv --name icpac-act --inspect
    python manage.py import_water --file data/raw/water/ken_waterpoints1.csv --name icpac-act \
        --id-col ID --name-col NAME --type-col TYPE [--status-col STATUS] [--lat-col Y --lon-col X]

Re-imports update location/name/kind only; verification fields are never overwritten.
"""
import csv
import json
import os
import re

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from shapely.geometry import shape

from .models import WaterPoint
from .views import _layers, _q

OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
# Overpass rejects anonymous clients (HTTP 406): identify the app, ideally with a contact (TERRA_CONTACT env var).
HEADERS = {"User-Agent": f"TerraRange/0.1 ({os.environ.get('TERRA_CONTACT', 'water-point import')})",
           "Accept": "application/json"}
KINDS = [(r"bore", "borehole"), (r"\bwell\b", "well"), (r"spring", "spring"),
         (r"\b(dam|pan|reservoir|pond|lake|basin)\b", "surface water"), (r"water ?hole", "waterhole"),
         (r"\btap\b|kiosk|drinking|water point", "tap"), (r"pump", "well")]
LAT, LON = ("lat", "latitude", "y"), ("lon", "lng", "longitude", "x")


def kind_of(text):
    t = (text or "").lower().replace("_", " ")
    return next((k for pat, k in KINDS if re.search(pat, t)), "other")


def status_of(text):
    t = (text or "").lower()
    if re.search(r"non.?function|not function|broken|defunct|abandon|dry|dead", t):
        return "non_functional"
    return "functional" if re.search(r"function|working|operational", t) else "unknown"


def pick(row, names, override=None):
    for n in ([override] if override else []) + list(names):
        for k in row:
            if k and k.lower() == n.lower() and row[k] not in (None, ""):
                return row[k]
    return None


def county_bbox():
    c = _layers()["wards"]
    g = _q(c["geom"])
    with connection.cursor() as cur:
        cur.execute(f"SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e) FROM "
                    f"(SELECT ST_Extent(ST_Transform({g}, 4326))::geometry AS e FROM public.{_q(c['table'])}) s")
        return cur.fetchone()


def osm_rows():
    w, s, e, n = county_bbox()
    b = f"({s},{w},{n},{e})"
    q = ("[out:json][timeout:180];(" + "".join(f"nwr{t}{b};" for t in (
        '["man_made"="water_well"]', '["amenity"="drinking_water"]', '["amenity"="water_point"]',
        '["natural"="spring"]', '["man_made"="water_tap"]',
        '["natural"="water"]["water"~"reservoir|pond|lake|basin"]')) + ");out center tags;")
    errors = []
    for url in OVERPASS:                                    # try each public server in turn
        try:
            r = requests.post(url, data={"data": q}, headers=HEADERS, timeout=240)
            r.raise_for_status()
            elements = r.json().get("elements", [])
            break
        except (requests.RequestException, ValueError) as e:
            errors.append(f"{url}: {e}")
    else:
        raise CommandError("Overpass failed on every server:\n  " + "\n  ".join(errors))
    for el in elements:
        tags = el.get("tags", {})
        c = el.get("center") or el
        if "lat" not in c:
            continue
        text = " ".join(tags.get(k, "") for k in ("man_made", "amenity", "natural", "water"))
        yield (f"{el['type']}/{el['id']}", c["lat"], c["lon"], tags.get("name") or tags.get("name:en") or "",
               kind_of(text), "unknown", tags)


def file_rows(o):
    path = o["file"]
    if path.lower().endswith((".geojson", ".json")):
        src = [(f.get("id", i), shape(f["geometry"]), f.get("properties") or {})
               for i, f in enumerate(json.load(open(path, encoding="utf-8"))["features"])]
        src = [(i, (g if g.geom_type == "Point" else g.representative_point()), p) for i, g, p in src]
        rows = [(i, g.y, g.x, p) for i, g, p in src]
    else:
        rows = []
        for i, r in enumerate(csv.DictReader(open(path, encoding="utf-8-sig"))):
            la, lo = pick(r, LAT, o["lat_col"]), pick(r, LON, o["lon_col"])
            if la and lo:
                rows.append((i, float(la), float(lo), r))
    if o["inspect"]:
        print("columns:", list(rows[0][3].keys()) if rows else "none found (check lat/lon columns)")
        for r in rows[:3]:
            print(r)
        return
    for i, la, lo, p in rows:
        yield (str(pick(p, [], o["id_col"]) or i), la, lo, str(pick(p, ["name"], o["name_col"]) or ""),
               kind_of(str(pick(p, ["type", "kind", "category"], o["type_col"]) or "")),
               status_of(str(pick(p, ["status", "functionality"], o["status_col"]) or "")), p)


def tag_wards():
    c = _layers()["wards"]
    g, code = _q(c["geom"]), _q(c["code"])
    with connection.cursor() as cur:
        cur.execute(f"""UPDATE geography_waterpoint p SET ward_code = w.{code}::text
                        FROM public.{_q(c['table'])} w
                        WHERE ST_Intersects(ST_SetSRID(ST_MakePoint(p.lon, p.lat), 4326), ST_Transform(w.{g}, 4326))""")
        return cur.rowcount


class Command(BaseCommand):
    help = "Import water points (OpenStreetMap or a CSV/GeoJSON file) and tag them with wards."

    def add_arguments(self, p):
        p.add_argument("--source", choices=["osm"])
        p.add_argument("--file")
        p.add_argument("--name", help="source label stored with each point, e.g. icpac-act")
        for a in ("id-col", "name-col", "type-col", "status-col", "lat-col", "lon-col"):
            p.add_argument(f"--{a}")
        p.add_argument("--inspect", action="store_true", help="print columns and sample rows, then stop")

    def handle(self, *a, **o):
        if o["source"] == "osm":
            name, rows = "osm", osm_rows()
        elif o["file"]:
            if not o["name"]:
                raise CommandError("--name is required with --file")
            name, rows = o["name"], file_rows(o)
        else:
            raise CommandError("use --source osm or --file PATH --name LABEL")
        if o["inspect"]:
            for _ in rows or []:
                pass
            return
        objs = [WaterPoint(source=name, source_id=sid[:80], name=nm[:160], kind=kd, lat=la, lon=lo,
                           reported_status=st, raw={k: v for k, v in (raw or {}).items() if isinstance(v, (str, int, float))})
                for sid, la, lo, nm, kd, st, raw in rows]
        WaterPoint.objects.bulk_create(objs, batch_size=1000, update_conflicts=True,
                                       unique_fields=["source", "source_id"],
                                       update_fields=["name", "kind", "lat", "lon", "raw"])
        tagged = tag_wards()
        qs = WaterPoint.objects.filter(source=name)
        self.stdout.write(f"{name}: {len(objs)} read, {qs.count()} stored, {qs.filter(ward_code__isnull=True).count()} outside the wards")
        for k in sorted({x.kind for x in objs}):
            self.stdout.write(f"  {k}: {qs.filter(kind=k).count()}")