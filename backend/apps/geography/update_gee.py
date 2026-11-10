"""Pull ward-level data through the Earth Engine Python API (server-side reductions, small results).

    pip install earthengine-api
    earthengine authenticate                  # once
    export EE_PROJECT=<your-earth-engine-cloud-project>
    python manage.py update_gee                       # everything, incremental
    python manage.py update_gee --only ndvi rain      # just the time series
    python manage.py update_gee --only static season  # surface water + population + point seasonality
    python manage.py update_gee --full                # re-fetch all history

Ward geometries and water points are read from your database and sent to Earth Engine, so no asset upload is needed.
Writes the same WardIndicator rows as update_indicators (GEE overwrites matching periods), then re-derives VCI/SPI.
"""
import datetime as dt
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.models import Max
from django.utils import timezone

from .models import IngestRun, WardIndicator, WardStat, WaterPoint
from .views import _layers, _q
from .indicator_logic import MIN_VALID, derive, save

NDVI_START, RAIN_START, POP_YEAR, NEAR_KM = 2001, 1991, 2020, (5, 10, 20)


def init_ee():
    try:
        import ee
    except ImportError:
        raise CommandError("Run: pip install earthengine-api")
    project = os.environ.get("EE_PROJECT")
    if not project:
        raise CommandError("Set EE_PROJECT to your Earth Engine Cloud project id")
    try:
        ee.Initialize(project=project)
    except Exception as e:
        raise CommandError(f"Earth Engine could not start ({e}). Run: earthengine authenticate")
    return ee


def get(obj, tries=4):
    """getInfo with retries (EE occasionally times out on large reductions)."""
    for i in range(tries):
        try:
            return obj.getInfo()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def ward_geoms():
    c = _layers()["wards"]
    g, code = _q(c["geom"]), _q(c["code"])
    with connection.cursor() as cur:
        cur.execute(f"SELECT {code}::text, ST_AsGeoJSON(ST_SimplifyPreserveTopology(ST_Transform({g}, 4326), 0.0003), 5) "
                    f"FROM public.{_q(c['table'])} WHERE {g} IS NOT NULL")
        return cur.fetchall()


def ward_fc(ee, geoms):
    return ee.FeatureCollection([ee.Feature(ee.Geometry(json.loads(gj), None, False), {"code": code}) for code, gj in geoms])


# ---------------- time series ----------------
def ndvi_year(ee, wards, y):
    def prep(img):
        good = img.select("SummaryQA").lte(1)                       # 0 good, 1 marginal
        return (img.select("NDVI").multiply(0.0001).updateMask(good).rename("ndvi")
                .addBands(good.rename("valid")).set("date", img.date().format("YYYY-MM-dd")))
    col = ee.ImageCollection("MODIS/061/MOD13Q1").filterDate(f"{y}-01-01", f"{y + 1}-01-01").map(prep)
    rows = col.map(lambda img: img.reduceRegions(collection=wards, reducer=ee.Reducer.mean(), scale=250, tileScale=4)
                   .map(lambda f: f.set("date", img.get("date")).setGeometry(None))).flatten()
    return get(rows)["features"]


def rain_year(ee, wards, y):
    today = dt.date.today()
    last = 11 if y < today.year else today.month - 2                 # skip the current, incomplete month
    if last < 0:
        return []
    chirps = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")

    def month(n):
        s = ee.Date.fromYMD(y, 1, 1).advance(n, "month")
        return chirps.filterDate(s, s.advance(1, "month")).sum().rename("rain_mm").set("date", s.format("YYYY-MM-dd"))
    col = ee.ImageCollection(ee.List.sequence(0, last).map(month))
    rows = col.map(lambda img: img.reduceRegions(collection=wards, reducer=ee.Reducer.mean(), scale=5566, tileScale=4)
                   .map(lambda f: f.set("date", img.get("date")).setGeometry(None))).flatten()
    return get(rows)["features"]


def ndvi_rows(feats):
    out = []
    for f in feats:
        p = f["properties"]
        d = dt.date.fromisoformat(p["date"])
        if (d.timetuple().tm_yday - 1) % 16:                         # Terra composites only (DOY 1, 17, 33, ...)
            continue
        nd, valid = p.get("ndvi"), p.get("valid")
        ok = nd is not None and valid is not None and valid >= MIN_VALID
        out.append(dict(ward_code=p["code"], indicator="ndvi", period_start=d, period_end=d + dt.timedelta(days=15),
                        value=float(nd) if ok else None, valid_fraction=valid, source="modis-061-gee"))
    return out


def rain_rows(feats):
    out = []
    for f in feats:
        p = f["properties"]
        d = dt.date.fromisoformat(p["date"])
        end = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
        out.append(dict(ward_code=p["code"], indicator="rain_mm", period_start=d, period_end=end,
                        value=p.get("rain_mm"), valid_fraction=None, source="chirps-v2-gee"))
    return out


# ---------------- static stats and point seasonality ----------------
def points_fc(ee):
    pts = list(WaterPoint.objects.filter(ward_code__isnull=False).values_list("id", "lat", "lon"))
    fc = ee.FeatureCollection([ee.Feature(ee.Geometry.Point([lo, la]), {"id": i}) for i, la, lo in pts]) if pts else None
    return pts, fc


def ward_static(ee, geoms, pts_fc):
    gsw = ee.Image("JRC/GSW1_4/GlobalSurfaceWater")
    km2, seas = ee.Image.pixelArea().divide(1e6), gsw.select("seasonality")
    water = ee.Image.cat([km2.rename("ward_area_km2"),
                          km2.updateMask(seas.eq(12)).rename("water_permanent_km2"),
                          km2.updateMask(seas.gt(0).And(seas.lt(12))).rename("water_seasonal_km2"),
                          km2.updateMask(gsw.select("max_extent").eq(1)).rename("water_max_extent_km2")])
    pop = (ee.ImageCollection("WorldPop/GP/100m/pop").filter(ee.Filter.eq("country", "KEN"))
           .filter(ee.Filter.eq("year", POP_YEAR)).first())
    bands = [pop.rename(f"pop_{POP_YEAR}")]
    if pts_fc is not None:                                           # straight-line distance to ANY mapped point
        far = max(NEAR_KM) * 1000
        dist = pts_fc.distance(searchRadius=far, maxError=100).unmask(far + 1)
        bands += [pop.updateMask(dist.lte(k * 1000)).rename(f"pop_within_{k}km_water") for k in NEAR_KM]
    popimg = ee.Image.cat(bands)

    def one(item):
        code, gj = item
        geom = ee.Geometry(json.loads(gj), None, False)
        w = get(water.reduceRegion(reducer=ee.Reducer.sum(), geometry=geom, scale=30, maxPixels=1e10, tileScale=8))
        p = get(popimg.reduceRegion(reducer=ee.Reducer.sum(), geometry=geom, scale=100, maxPixels=1e10, tileScale=4))
        return code, {**w, **p}
    with ThreadPoolExecutor(4) as ex:
        return list(ex.map(one, geoms))


def point_season(ee, pts):
    mr = ee.ImageCollection("JRC/GSW1_4/MonthlyRecurrence")
    key = "month" if "month" in get(mr.first().propertyNames()) else "system:index"   # images are numbered 1-12 either way
    def month_img(m):
        return mr.filter(ee.Filter.eq(key, m if key == "month" else str(m))).first()
    bands = [month_img(m).select("monthly_recurrence").rename(f"m{m}") for m in range(1, 13)]
    bands.append(ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("max_extent").rename("max_extent"))
    img, out = ee.Image.cat(bands), {}
    for i in range(0, len(pts), 400):
        fc = ee.FeatureCollection([ee.Feature(ee.Geometry.Point([lo, la]).buffer(90), {"id": pid})
                                   for pid, la, lo in pts[i:i + 400]])   # 90 m buffer tolerates offset point locations
        for f in get(img.reduceRegions(collection=fc, reducer=ee.Reducer.max(), scale=30, tileScale=4))["features"]:
            out[f["properties"]["id"]] = f["properties"]
    return out


def check(ee, out):
    """Quick pre-flight: Earth Engine works and the datasets look as expected."""
    out.write("Earth Engine started OK")
    modis = ee.ImageCollection("MODIS/061/MOD13Q1").sort("system:time_start", False)
    out.write(f"MOD13Q1 latest composite: {get(modis.first().date().format('YYYY-MM-dd'))}")
    chirps = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY").sort("system:time_start", False)
    out.write(f"CHIRPS latest day: {get(chirps.first().date().format('YYYY-MM-dd'))}")
    pop = ee.ImageCollection("WorldPop/GP/100m/pop").filter(ee.Filter.eq("country", "KEN")).filter(ee.Filter.eq("year", POP_YEAR))
    out.write(f"WorldPop KEN {POP_YEAR} images: {get(pop.size())}")
    mr = ee.ImageCollection("JRC/GSW1_4/MonthlyRecurrence")
    out.write(f"JRC monthly recurrence: {get(mr.size())} images, properties {get(mr.first().propertyNames())}")


class Command(BaseCommand):
    help = "Fetch NDVI, rainfall, surface water, population and point seasonality via the Earth Engine API."

    def add_arguments(self, p):
        p.add_argument("--only", nargs="+", choices=["ndvi", "rain", "static", "season"])
        p.add_argument("--full", action="store_true", help="re-fetch all history")
        p.add_argument("--no-derive", action="store_true")
        p.add_argument("--check", action="store_true", help="test Earth Engine access and dataset names, then stop")

    def handle(self, *a, **o):
        ee = init_ee()
        if o["check"]:
            return check(ee, self.stdout)
        run = IngestRun.objects.create(command="update_gee", params={k: o[k] for k in ("only", "full", "no_derive")})
        try:
            run.rows_written = self.run(ee, o)
            run.status = "ok"
        except Exception as e:
            run.status, run.notes = "failed", str(e)[:500]
            raise
        finally:
            run.finished_at = timezone.now()
            run.save()

    def run(self, ee, o):
        todo, written = set(o["only"] or ["ndvi", "rain", "static", "season"]), 0
        geoms = ward_geoms()
        wards = ward_fc(ee, geoms)
        self.stdout.write(f"{len(geoms)} wards sent to Earth Engine")
        today = dt.date.today()
        for key, ind, first, fetch, parse in (("ndvi", "ndvi", NDVI_START, ndvi_year, ndvi_rows),
                                              ("rain", "rain_mm", RAIN_START, rain_year, rain_rows)):
            if key not in todo:
                continue
            last = WardIndicator.objects.filter(indicator=ind).aggregate(m=Max("period_start"))["m"]
            start = first if o["full"] or not last else last.year
            years = list(range(start, today.year + 1))
            self.stdout.write(f"{key}: fetching {years[0]}-{years[-1]}")
            with ThreadPoolExecutor(4) as ex:
                feats = [f for chunk in ex.map(lambda y: fetch(ee, wards, y), years) for f in chunk]
            rows = parse(feats)
            save(rows)
            written += len(rows)
            self.stdout.write(f"  {key}: {len(rows)} ward-periods stored")
        if todo & {"static", "season"}:
            pts, pfc = points_fc(ee)
        if "static" in todo:
            res = ward_static(ee, geoms, pfc)
            objs = [WardStat(ward_code=c, stat=k, value=v, source="jrc-gsw1.4+worldpop",
                             reference_year=POP_YEAR if k.startswith("pop") else None) for c, d in res for k, v in d.items()]
            WardStat.objects.bulk_create(objs, update_conflicts=True, unique_fields=["ward_code", "stat"],
                                         update_fields=["value", "source", "reference_year"])
            written += len(objs)
            self.stdout.write(f"  static: {len(objs)} ward statistics stored")
        if "season" in todo and pts:
            info = point_season(ee, pts)
            rows = list(WaterPoint.objects.filter(id__in=info.keys()))
            for r in rows:
                p = info[r.id]
                r.season = [p.get(f"m{m}") for m in range(1, 13)]
                r.water_ever = bool(p.get("max_extent"))
            WaterPoint.objects.bulk_update(rows, ["season", "water_ever"], batch_size=500)
            written += len(rows)
            self.stdout.write(f"  season: {len(rows)} water points profiled")
        if todo & {"ndvi", "rain"} and not o["no_derive"]:
            self.stdout.write(f"Derived rows: {derive()}")
        return written
