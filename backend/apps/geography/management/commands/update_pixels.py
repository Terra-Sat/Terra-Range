"""Store pixel layers (VCI and/or NDVI) in the database, once per composite.

Two sources:
  --tif PATH   a north-up EPSG:4326 GeoTIFF (float, NaN or < -9000 = missing).
               One indicator per call, band chosen with --band.
  --ee         build the indicator(s) in Earth Engine and download them
               (UNTESTED against a live project, see below)

--indicator takes a comma list with --ee, e.g. "vci,ndvi". Both bands come down
in ONE download per composite, so adding NDVI does not double the Earth Engine calls.

Examples
  python manage.py update_pixels --inspect --ward-code-col <col>
  python manage.py update_pixels --tif vci.tif --period-start 2026-09-30 \
        --period-end 2026-10-15 --ward-code-col <col>
  python manage.py update_pixels --ee --indicator vci,ndvi --ward-code-col <col>
  python manage.py update_pixels --ee --indicator vci,ndvi --from-date 2025-01-01 \
        --ward-code-col <col>        # backfill, skipping what is already stored

CALIBRATION CHOICES (not validated, recorded in PixelLayer.params):
  VCI: baseline 2001-2020, SummaryQA <= 1 (good or marginal), mask where the
  baseline NDVI range is below 0.1. NDVI: SummaryQA <= 1 only. Change with flags.

LIMITATIONS
  * The --ee path was written from the Earth Engine docs and has NEVER been run
    against a live project (the sandbox cannot reach EE). Expect to adjust the
    download parameters on the first run. Nothing is written if it fails.
  * Re-running for the same period REPLACES that indicator's layer.
  * --from-date downloads one composite at a time and keeps going after a failure.
    Failures and fully masked composites are listed at the end and the command
    then exits with an error, so nothing is dropped silently. Per-composite time
    is unmeasured (my guess is tens of seconds each).
  * Class shares exist for VCI only. NDVI has no accepted classes.
  * IngestRun logging is not wired in: its fields have not been seen.
"""
import datetime as dt
import io
import os
import tempfile
import zipfile

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from ... import pixel_store as ps

COL = "MODIS/061/MOD13Q1"            # Terra only (MYD13Q1 is Aqua)
PIXEL_DEG = 231.656358 / 111319.49   # MODIS 250 m in degrees at the equator

_EE_READY = False


def _ee_init():
    global _EE_READY
    if not _EE_READY:
        import ee
        ee.Initialize(project=os.environ["EE_PROJECT"])
        _EE_READY = True


def list_ee_composites(from_date):
    """Start dates of every MOD13Q1 composite from from_date to the latest."""
    import ee
    _ee_init()
    end = dt.date.today() + dt.timedelta(days=1)
    col = ee.ImageCollection(COL).filterDate(from_date.isoformat(), end.isoformat())
    ms = col.aggregate_array("system:time_start").getInfo()
    return sorted({dt.datetime.fromtimestamp(m / 1000, dt.timezone.utc).date() for m in ms})


def fetch_ee_tif(bbox, out_path, *, indicators, period_start, baseline, min_range, qa_max):
    """Download one composite as a multi-band float GeoTIFF, bands in `indicators`
    order. Returns (start, end)."""
    import ee
    import requests

    _ee_init()
    col = ee.ImageCollection(COL)
    if period_start:
        nxt = period_start + dt.timedelta(days=1)
        col = col.filterDate(period_start.isoformat(), nxt.isoformat())
    else:
        col = col.sort("system:time_start", False)
    if col.size().getInfo() == 0:
        raise CommandError("no MOD13Q1 composite found for that period")
    img = ee.Image(col.first())
    t_ms = img.get("system:time_start").getInfo()
    start = dt.datetime.fromtimestamp(t_ms / 1000, dt.timezone.utc).date()
    end = min(start + dt.timedelta(days=15), dt.date(start.year, 12, 31))
    doy = start.timetuple().tm_yday

    def ndvi(i):
        v = i.select("NDVI").multiply(0.0001)
        return v.updateMask(i.select("SummaryQA").lte(qa_max))

    bands = []
    for ind in indicators:
        if ind == "ndvi":
            bands.append(ndvi(img).toFloat().unmask(-9999).rename("ndvi"))
        elif ind == "vci":
            base = (ee.ImageCollection(COL)
                    .filter(ee.Filter.calendarRange(doy, doy, "day_of_year"))
                    .filter(ee.Filter.calendarRange(baseline[0], baseline[1], "year"))
                    .map(ndvi))
            lo, hi = base.min(), base.max()
            rng = hi.subtract(lo)
            vci = (ndvi(img).subtract(lo).divide(rng).multiply(100)
                   .updateMask(rng.gte(min_range)).toFloat().unmask(-9999))
            bands.append(vci.rename("vci"))
        else:
            raise CommandError(f"no Earth Engine recipe for indicator {ind!r}")
    image = ee.Image.cat(bands)

    west, south, east, north = bbox
    region = ee.Geometry.Rectangle([west, south, east, north], "EPSG:4326", False)
    url = image.getDownloadURL({
        "name": f"pix_{start.isoformat()}", "format": "GEO_TIFF", "region": region,
        "crs": "EPSG:4326", "filePerBand": False,
        "crs_transform": [PIXEL_DEG, 0, west, 0, -PIXEL_DEG, north]})
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    # a failed EE download can return JSON or HTML with HTTP 200
    if r.content[:4] not in (b"II*\x00", b"MM\x00*", b"PK\x03\x04"):
        raise CommandError(f"download is not a GeoTIFF: {r.content[:200]!r}")
    data = r.content
    if data[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(data))
        tifs = [n for n in z.namelist() if n.lower().endswith((".tif", ".tiff"))]
        if len(tifs) != 1:
            raise CommandError(f"expected one GeoTIFF in the zip, got {tifs}. "
                               "EE ignored filePerBand=False; adjust the download call.")
        data = z.read(tifs[0])
    with open(out_path, "wb") as f:
        f.write(data)
    return start, end


class Command(BaseCommand):
    help = "Store pixel layers (VCI, NDVI) in the database."

    def add_arguments(self, p):
        p.add_argument("--tif")
        p.add_argument("--band", type=int, default=1, help="band number in --tif")
        p.add_argument("--ee", action="store_true")
        p.add_argument("--indicator", default="vci",
                       help="vci, ndvi, or a comma list with --ee (band order = list order)")
        p.add_argument("--period-start")
        p.add_argument("--period-end")
        p.add_argument("--methodology-version", default="pixvci-0.1")
        p.add_argument("--source", default=None)
        p.add_argument("--ward-code-col")
        p.add_argument("--ward-table", default="public.wards")
        p.add_argument("--no-shares", action="store_true",
                       help="skip ward class shares (explicit, never silent)")
        p.add_argument("--bbox", nargs=4, type=float, metavar=("W", "S", "E", "N"))
        p.add_argument("--baseline-start", type=int, default=2001)
        p.add_argument("--baseline-end", type=int, default=2020)
        p.add_argument("--min-range", type=float, default=0.1)
        p.add_argument("--qa-max", type=int, default=1)
        p.add_argument("--from-date", help="EE backfill: every composite from this date")
        p.add_argument("--overwrite", action="store_true",
                       help="with --from-date, re-ingest periods already stored")
        p.add_argument("--keep-tif")
        p.add_argument("--inspect", action="store_true",
                       help="list columns of the ward table and exit")

    def handle(self, *a, **o):
        if o["inspect"]:
            return self._inspect(o["ward_table"])
        if bool(o["tif"]) == bool(o["ee"]):
            raise CommandError("give exactly one of --tif or --ee")
        inds = [x.strip() for x in o["indicator"].split(",") if x.strip()]
        for ind in inds:
            try:
                ps.spec(ind)
            except ValueError as e:
                raise CommandError(str(e))
        if len(set(inds)) != len(inds):
            raise CommandError("duplicate indicator in --indicator")
        if o["tif"] and len(inds) != 1:
            raise CommandError("--tif takes one indicator; pick the band with --band")
        if o["from_date"] and (not o["ee"] or o["period_start"]):
            raise CommandError("--from-date needs --ee and cannot be combined with --period-start")
        needs_shares = any(ps.spec(i)["classed"] for i in inds)
        if needs_shares and not o["no_shares"] and not o["ward_code_col"]:
            raise CommandError("pass --ward-code-col (see --inspect) or --no-shares "
                               "to skip per-ward class shares explicitly")

        geoms = None
        if o["ward_code_col"] and not o["no_shares"]:
            geoms = ps.load_ward_geoms(o["ward_code_col"], o["ward_table"])
            self.stdout.write(f"loaded {len(geoms)} ward geometries")
        if "ndvi" in inds:
            self.stdout.write("note: NDVI has no class shares (no accepted NDVI classes)")

        pstart = dt.date.fromisoformat(o["period_start"]) if o["period_start"] else None
        pend = dt.date.fromisoformat(o["period_end"]) if o["period_end"] else None

        if o["tif"]:
            if not (pstart and pend):
                raise CommandError("--tif needs --period-start and --period-end")
            self._ingest(o, o["tif"], pstart, pend, o["source"] or "GeoTIFF supplied by user",
                         geoms, [(inds[0], o["band"])])
            return

        bbox = self._bbox(o, geoms)
        source = o["source"] or f"Earth Engine {COL}, Terra only"
        if o["from_date"]:
            return self._backfill(o, inds, bbox, source, geoms)
        tif = o["keep_tif"] or os.path.join(tempfile.mkdtemp(), "pix.tif")
        pstart, pend = fetch_ee_tif(
            bbox, tif, indicators=inds, period_start=pstart,
            baseline=(o["baseline_start"], o["baseline_end"]),
            min_range=o["min_range"], qa_max=o["qa_max"])
        self.stdout.write(f"downloaded composite {pstart} to {pend}")
        self._ingest(o, tif, pstart, pend, source, geoms, list(zip(inds, range(1, len(inds) + 1))))

    def _bbox(self, o, geoms):
        if o["bbox"]:
            return tuple(o["bbox"])
        if not geoms:
            raise CommandError("--ee needs --bbox or ward geometries to set the extent")
        from shapely.geometry import shape
        bs = [shape(g).bounds for g in geoms.values()]
        pad = 0.01
        return (min(b[0] for b in bs) - pad, min(b[1] for b in bs) - pad,
                max(b[2] for b in bs) + pad, max(b[3] for b in bs) + pad)

    def _params(self, o, ind):
        if ind == "vci":
            return {"baseline": [o["baseline_start"], o["baseline_end"]],
                    "min_range": o["min_range"], "qa_max": o["qa_max"],
                    "quantisation": ps.ENCODING, "breaks": list(ps.get_breaks())}
        return {"qa_max": o["qa_max"], "quantisation": ps.spec(ind)["encoding"]}

    def _backfill(self, o, inds, bbox, source, geoms):
        from ...models import PixelLayer
        starts = list_ee_composites(dt.date.fromisoformat(o["from_date"]))
        have = {i: set(PixelLayer.objects.filter(indicator=i).values_list("period_start", flat=True))
                for i in inds}
        todo = {d: [i for i in inds if o["overwrite"] or d not in have[i]] for d in starts}
        todo = {d: m for d, m in todo.items() if m}
        self.stdout.write(f"{len(starts)} composites from {o['from_date']}, "
                          f"{len(starts) - len(todo)} complete for {inds}, {len(todo)} to fetch")
        failed, masked = [], []
        tmp = tempfile.mkdtemp()
        for n, (d, missing) in enumerate(sorted(todo.items()), 1):
            self.stdout.write(f"[{n}/{len(todo)}] {d} -> {missing}")
            tif = os.path.join(tmp, f"pix_{d}.tif")
            try:
                start, end = fetch_ee_tif(
                    bbox, tif, indicators=inds, period_start=d,
                    baseline=(o["baseline_start"], o["baseline_end"]),
                    min_range=o["min_range"], qa_max=o["qa_max"])
                bands = [(i, k) for k, i in enumerate(inds, 1) if i in missing]
                masked += self._ingest(o, tif, start, end, source, geoms, bands, collect=True)
            except Exception as e:           # keep going, report at the end
                failed.append((d, f"{type(e).__name__}: {e}"))
            finally:
                if os.path.exists(tif):
                    os.remove(tif)
        for d, ind in masked:
            self.stdout.write(self.style.WARNING(f"fully masked, not stored: {ind} {d}"))
        for d, why in failed:
            self.stdout.write(self.style.ERROR(f"failed: {d} {why}"))
        if failed or masked:
            raise CommandError(f"{len(failed)} failed and {len(masked)} fully masked of "
                               f"{len(todo)} composites. Re-run the same command to retry "
                               "(stored ones are skipped).")

    def _ingest(self, o, tif, pstart, pend, source, geoms, bands, collect=False):
        """Store each (indicator, band). With collect=True fully masked rasters are
        returned as [(period, indicator)] instead of raising."""
        masked = []
        for ind, band in bands:
            arr, bounds = ps.read_geotiff(tif, band=band)
            try:
                layer, shares = ps.store_layer(
                    indicator=ind, period_start=pstart, period_end=pend,
                    bounds=bounds, arr_float=arr, source=source,
                    methodology_version=o["methodology_version"],
                    params=self._params(o, ind), ward_geoms=geoms)
            except ps.EmptyRaster:
                if not collect:
                    raise
                masked.append((pstart, ind))
                continue
            valid = 100.0 * (arr == arr).sum() / arr.size
            self.stdout.write(self.style.SUCCESS(
                f"stored {ind} layer {layer.pk} ({pstart}): {layer.width}x{layer.height}, "
                f"{valid:.1f}% valid, {len(bytes(layer.values)) / 1024:.0f} KB compressed, "
                f"status {layer.status}"))
            empty = [c for c, s in shares.items() if s["n_total"] == 0]
            low = [c for c, s in shares.items() if s["coverage"] is not None and s["coverage"] < 50]
            if empty:
                self.stdout.write(self.style.WARNING(f"wards with no pixel centre: {empty}"))
            if low:
                self.stdout.write(self.style.WARNING(f"wards under 50% valid coverage: {low}"))
        return masked

    def _inspect(self, table):
        schema, _, name = table.rpartition(".")
        with connection.cursor() as cur:
            cur.execute("SELECT column_name, data_type FROM information_schema.columns "
                        "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
                        [schema or "public", name])
            for col, typ in cur.fetchall():
                self.stdout.write(f"{col:30s} {typ}")
