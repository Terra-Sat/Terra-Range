"""Load raw files written by fetch_open_data.py into the Django database.

    python manage.py ingest_open_data                    # process everything in data/raw/
    python manage.py ingest_open_data --raw data/raw     # explicit path
    python manage.py ingest_open_data --only timeseries  # timeseries only
    python manage.py ingest_open_data --only static      # static ward stats only
    python manage.py ingest_open_data --only climate     # ENSO/IOD climate drivers only

Dataset → model mapping
------------------------
timeseries/chirps_monthly_mm.csv          → WardIndicator  indicator=rain_mm
timeseries/modis_ndvi_terra_16day.csv     → WardIndicator  indicator=ndvi, evi
timeseries/modis_lst_8day.csv             → WardIndicator  indicator=lst_day_k, lst_night_k
timeseries/modis_npp_annual.csv           → WardIndicator  indicator=npp_kgc_m2
timeseries/wapor_npp_dekadal_v2_raw.csv   → WardIndicator  indicator=npp_dn_wapor
timeseries/era5land_monthly.csv           → WardIndicator  one indicator per ERA5 variable
timeseries/nasa_power_monthly_*.csv       → WardIndicator  one indicator per POWER parameter
static/worldpop_ward_population_*.csv     → WardStat       stat=pop_<year>
static/ward_static_water_land_terrain.csv → WardStat       one stat per column
climate_drivers/*.data / *.txt            → ClimateIndex   (zlib-compressed raw text in DB)
static/glw4_*.tif                         → WardStat       zonal mean via rasterio (optional)

Compression strategy
--------------------
* WardIndicator / WardStat hold plain float values – no binary storage needed; the DB
  compresses column values with TOAST automatically (Postgres) or page compression (SQLite).
* ClimateIndex stores the entire raw text file compressed with zlib so the table never
  grows beyond a few hundred KB regardless of source length, and the original can be
  recovered exactly.  The model is defined in this file and needs a migration; run
  python manage.py makemigrations geography  after first use.
* GLW4 GeoTIFFs: zonal mean is computed in memory (rasterio) and stored as a WardStat
  float, so no binary is written to the DB at all.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import zlib
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, models
from django.utils import timezone

from ...models import IngestRun, WardIndicator, WardStat
from ...views import _layers, _q

# --------------------------------------------------------------------------- constants
METH = "open-data-1.0"
BATCH = 2000

# ERA5 variables as they appear in the CSV header
ERA5_COLS = [
    "temperature_2m",
    "total_precipitation_sum",
    "volumetric_soil_water_layer_1",
    "volumetric_soil_water_layer_2",
    "potential_evaporation_sum",
]

# NASA POWER parameters as they appear in the CSV header
POWER_COLS = ["T2M", "T2M_MAX", "T2M_MIN", "PRECTOTCORR", "RH2M", "ALLSKY_SFC_SW_DWN", "WS2M"]


# --------------------------------------------------------------------------- helpers
def _load_ward_codes() -> set[str]:
    """Return all ward codes currently registered in the spatial table."""
    try:
        c = _layers()["wards"]
        g, code = _q(c["geom"]), _q(c["code"])
        with connection.cursor() as cur:
            cur.execute(f"SELECT {code}::text FROM public.{_q(c['table'])} WHERE {g} IS NOT NULL")
            return {r[0] for r in cur.fetchall()}
    except Exception:
        return set()  # no spatial table – accept all ward codes


def _safe_float(v) -> float | None:
    """Convert to float; return None for missing / sentinel values."""
    if v is None or str(v).strip() in ("", "NA", "nan", "None"):
        return None
    try:
        f = float(v)
        return None if f <= -990 else f  # NASA POWER missing sentinel
    except (ValueError, TypeError):
        return None


def _period_end(start: dt.date, freq: str) -> dt.date:
    """Approximate period end for a given frequency label."""
    if freq == "16day":
        return start + dt.timedelta(days=15)
    if freq == "8day":
        return start + dt.timedelta(days=7)
    if freq == "monthly":
        # last day of the month
        import calendar
        last = calendar.monthrange(start.year, start.month)[1]
        return start.replace(day=last)
    if freq == "dekadal":
        return start + dt.timedelta(days=9)
    return start  # annual / unknown


def _save_indicators(rows: list[dict]) -> int:
    """Bulk-upsert WardIndicator rows.  Returns number written."""
    if not rows:
        return 0
    objs = [WardIndicator(methodology_version=METH, **r) for r in rows]
    WardIndicator.objects.bulk_create(
        objs, batch_size=BATCH, update_conflicts=True,
        unique_fields=["ward_code", "indicator", "period_start"],
        update_fields=["period_end", "value", "valid_fraction", "source", "methodology_version"],
    )
    return len(rows)


def _save_stats(rows: list[dict]) -> int:
    """Bulk-upsert WardStat rows.  Returns number written."""
    if not rows:
        return 0
    objs = [WardStat(**r) for r in rows]
    WardStat.objects.bulk_create(
        objs, batch_size=BATCH, update_conflicts=True,
        unique_fields=["ward_code", "stat"],
        update_fields=["value", "source", "reference_year"],
    )
    return len(rows)


# --------------------------------------------------------------------------- time-series loaders
def load_chirps(path: Path, log) -> int:
    """chirps_monthly_mm.csv  →  WardIndicator(indicator='rain_mm')"""
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            start = dt.date.fromisoformat(r["date"])
            v = _safe_float(r.get("rain_mm"))
            rows.append(dict(
                ward_code=r["code"], indicator="rain_mm",
                period_start=start, period_end=_period_end(start, "monthly"),
                value=v, valid_fraction=None, source="chirps-v2-open",
            ))
    n = _save_indicators(rows)
    log(f"  chirps_monthly: {n} ward-months")
    return n


def load_ndvi(path: Path, log) -> int:
    """modis_ndvi_terra_16day.csv  →  WardIndicator(indicator='ndvi'/'evi')"""
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            start = dt.date.fromisoformat(r["date"])
            end = _period_end(start, "16day")
            valid = _safe_float(r.get("valid"))
            for ind in ("ndvi", "evi"):
                v = _safe_float(r.get(ind))
                rows.append(dict(
                    ward_code=r["code"], indicator=ind,
                    period_start=start, period_end=end,
                    value=v, valid_fraction=valid, source="modis-13Q1-open",
                ))
    n = _save_indicators(rows)
    log(f"  modis_ndvi: {n} ward-period rows (ndvi + evi)")
    return n


def load_lst(path: Path, log) -> int:
    """modis_lst_8day.csv  →  WardIndicator(indicator='lst_day_k'/'lst_night_k')"""
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            start = dt.date.fromisoformat(r["date"])
            end = _period_end(start, "8day")
            for ind in ("lst_day_k", "lst_night_k"):
                v = _safe_float(r.get(ind))
                rows.append(dict(
                    ward_code=r["code"], indicator=ind,
                    period_start=start, period_end=end,
                    value=v, valid_fraction=None, source="modis-11A2-open",
                ))
    n = _save_indicators(rows)
    log(f"  modis_lst: {n} ward-period rows")
    return n


def load_modis_npp(path: Path, log) -> int:
    """modis_npp_annual.csv  →  WardIndicator(indicator='npp_kgc_m2')"""
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            start = dt.date.fromisoformat(r["date"])
            v = _safe_float(r.get("npp_kgc_m2"))
            rows.append(dict(
                ward_code=r["code"], indicator="npp_kgc_m2",
                period_start=start, period_end=start.replace(month=12, day=31),
                value=v, valid_fraction=None, source="modis-17A3HGF-open",
            ))
    n = _save_indicators(rows)
    log(f"  modis_npp: {n} ward-year rows")
    return n


def load_wapor_npp(path: Path, log) -> int:
    """wapor_npp_dekadal_v2_raw.csv  →  WardIndicator(indicator='npp_dn_wapor')"""
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            start = dt.date.fromisoformat(r["date"])
            v = _safe_float(r.get("npp_dn"))
            rows.append(dict(
                ward_code=r["code"], indicator="npp_dn_wapor",
                period_start=start, period_end=_period_end(start, "dekadal"),
                value=v, valid_fraction=None, source="wapor-v2-open",
            ))
    n = _save_indicators(rows)
    log(f"  wapor_npp: {n} ward-dekad rows")
    return n


def load_era5(path: Path, log) -> int:
    """era5land_monthly.csv  →  one WardIndicator per ERA5 variable."""
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        # header may have only a subset of ERA5_COLS depending on the fetch run
        present = [c for c in ERA5_COLS if c in (reader.fieldnames or [])]
        for r in reader:
            start = dt.date.fromisoformat(r["date"])
            end = _period_end(start, "monthly")
            for col in present:
                v = _safe_float(r.get(col))
                rows.append(dict(
                    ward_code=r["code"], indicator=f"era5_{col[:24]}",
                    period_start=start, period_end=end,
                    value=v, valid_fraction=None, source="era5-land-open",
                ))
    n = _save_indicators(rows)
    log(f"  era5land: {n} ward-month indicator rows ({len(present)} variables)")
    return n


def load_nasa_power(path: Path, log) -> int:
    """nasa_power_monthly_ward_centroids.csv  →  one WardIndicator per POWER parameter."""
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        present = [c for c in POWER_COLS if c in (reader.fieldnames or [])]
        for r in reader:
            # column is YYYY-MM  (e.g. 2023-07)
            month_str = r.get("month", "")
            try:
                start = dt.date.fromisoformat(f"{month_str}-01")
            except ValueError:
                continue
            end = _period_end(start, "monthly")
            for col in present:
                v = _safe_float(r.get(col))
                rows.append(dict(
                    ward_code=r["code"], indicator=f"power_{col.lower()[:20]}",
                    period_start=start, period_end=end,
                    value=v, valid_fraction=None, source="nasa-power-open",
                ))
    n = _save_indicators(rows)
    log(f"  nasa_power: {n} ward-month indicator rows ({len(present)} parameters)")
    return n


# --------------------------------------------------------------------------- static loaders
def load_worldpop(path: Path, log) -> int:
    """worldpop_ward_population_2000_2020.csv  →  WardStat(stat='pop_<year>')"""
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            v = _safe_float(r.get("population"))
            rows.append(dict(
                ward_code=r["code"],
                stat=f"pop_{r['year']}",
                value=v,
                source="worldpop-open",
                reference_year=int(r["year"]),
            ))
    n = _save_stats(rows)
    log(f"  worldpop: {n} ward-year stat rows")
    return n


def load_ward_static(path: Path, log) -> int:
    """ward_static_water_land_terrain.csv  →  WardStat (one row per column per ward)."""
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        stat_cols = [c for c in (reader.fieldnames or []) if c != "code"]
        for r in reader:
            for col in stat_cols:
                v = _safe_float(r.get(col))
                # truncate stat name to 40 chars (model max_length)
                rows.append(dict(
                    ward_code=r["code"],
                    stat=col[:40],
                    value=v,
                    source="jrc-srtm-worldcover-open",
                    reference_year=None,
                ))
    n = _save_stats(rows)
    log(f"  ward_static: {n} stat rows ({len(stat_cols)} columns)")
    return n


def load_glw4(raw_dir: Path, log) -> int:
    """static/glw4_*.tif  →  WardStat zonal means (requires rasterio + spatial table)."""
    try:
        import rasterio
        from rasterio.features import rasterize
        from rasterio.windows import from_bounds
        import numpy as np
    except ImportError:
        log("  glw4: skipped (pip install rasterio)")
        return 0

    tifs = list((raw_dir / "static").glob("glw4_*.tif"))
    if not tifs:
        log("  glw4: no GeoTIFF files found in static/")
        return 0

    # Load ward geometries from the spatial table
    try:
        c = _layers()["wards"]
        g, code = _q(c["geom"]), _q(c["code"])
        with connection.cursor() as cur:
            cur.execute(
                f"SELECT {code}::text, ST_AsGeoJSON(ST_Transform({g}, 4326), 5) "
                f"FROM public.{_q(c['table'])} WHERE {g} IS NOT NULL"
            )
            ward_rows = cur.fetchall()
    except Exception as e:
        log(f"  glw4: cannot load ward geometries ({e}); skipped")
        return 0

    import json
    from shapely.geometry import shape, mapping
    from shapely.ops import transform as shp_transform
    from pyproj import Transformer

    wards = [(code, shape(json.loads(gj))) for code, gj in ward_rows]
    rows = []

    for tif in tifs:
        # derive a short stat name, e.g. glw4_cattle_heads_km2
        label = tif.stem.replace("2015_dasymetric_kenya", "").strip("_")
        stat_name = (label + "_heads_km2")[:40]
        source_tag = "glw4-open"
        with rasterio.open(tif) as src:
            to_src = Transformer.from_crs(4326, src.crs, always_xy=True).transform
            nodata = src.nodata
            for ward_code, geom in wards:
                geom_proj = shp_transform(to_src, geom)
                b = geom_proj.bounds
                try:
                    win = from_bounds(*b, transform=src.transform).round_offsets().round_lengths()
                    data = src.read(1, window=win)
                    tf = src.window_transform(win)
                except Exception:
                    continue
                # rasterize the ward polygon
                mask = rasterize([(mapping(geom_proj), 1)], out_shape=data.shape,
                                 transform=tf, fill=0, dtype="uint8")
                vals = data[mask == 1]
                if nodata is not None:
                    vals = vals[vals != nodata]
                vals = vals[np.isfinite(vals) & (vals >= 0)]
                v = float(np.mean(vals)) if vals.size else None
                rows.append(dict(
                    ward_code=ward_code, stat=stat_name,
                    value=v, source=source_tag, reference_year=2015,
                ))
        log(f"  glw4 {tif.name}: {sum(1 for r in rows if r['stat'] == stat_name)} wards")

    n = _save_stats(rows)
    log(f"  glw4 total: {n} stat rows")
    return n


# --------------------------------------------------------------------------- climate driver loader
# ClimateIndex is defined inline here.  After first run:
#   python manage.py makemigrations geography
#   python manage.py migrate

class ClimateIndex(models.Model):
    """Raw climate driver index file (ONI, Nino3.4, DMI) stored compressed."""
    name = models.CharField(max_length=80, unique=True)          # e.g. "oni_cpc"
    source_file = models.CharField(max_length=120)               # relative path in data/raw
    provider = models.CharField(max_length=80, blank=True)
    # zlib-compressed UTF-8 of the original text file; decompress with zlib.decompress()
    data_compressed = models.BinaryField()
    # uncompressed byte count – lets callers know size without decompressing
    raw_bytes = models.PositiveIntegerField()
    # compression ratio for monitoring
    compressed_bytes = models.PositiveIntegerField()
    retrieved_at = models.DateField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "geography"

    def get_text(self) -> str:
        """Decompress and decode the stored payload."""
        return zlib.decompress(bytes(self.data_compressed)).decode("utf-8")

    @classmethod
    def store(cls, name: str, source_file: str, provider: str, raw: bytes) -> "ClimateIndex":
        compressed = zlib.compress(raw, level=9)
        obj, _ = cls.objects.update_or_create(
            name=name,
            defaults=dict(
                source_file=source_file,
                provider=provider,
                data_compressed=compressed,
                raw_bytes=len(raw),
                compressed_bytes=len(compressed),
                retrieved_at=dt.date.today(),
            ),
        )
        return obj


DRIVER_PROVIDERS = {
    "oni_cpc": "NOAA CPC",
    "nino34_anom_psl": "NOAA PSL",
    "dmi_hadisst_psl": "NOAA PSL",
}


def load_climate_indices(raw_dir: Path, log) -> int:
    climate_dir = raw_dir / "climate_drivers"
    if not climate_dir.is_dir():
        log("  climate_drivers: directory not found – skipping")
        return 0

    # Ensure the table exists (handles the case before makemigrations is run)
    try:
        ClimateIndex.objects.count()
    except Exception:
        log("  climate_drivers: ClimateIndex table not yet created; "
            "run makemigrations + migrate geography, then re-run this command")
        return 0

    n = 0
    for f in climate_dir.iterdir():
        if not f.is_file():
            continue
        raw = f.read_bytes()
        name = f.stem  # e.g. "oni_cpc" from "oni_cpc.txt"
        provider = DRIVER_PROVIDERS.get(name, "NOAA")
        obj = ClimateIndex.store(
            name=name,
            source_file=str(f.relative_to(raw_dir)),
            provider=provider,
            raw=raw,
        )
        ratio = obj.compressed_bytes / obj.raw_bytes if obj.raw_bytes else 1
        log(f"  climate index {name}: {obj.raw_bytes} bytes → "
            f"{obj.compressed_bytes} compressed ({ratio:.0%})")
        n += 1
    return n


# --------------------------------------------------------------------------- management command
class Command(BaseCommand):
    help = "Ingest raw open-data files (from fetch_open_data.py) into the database."

    def add_arguments(self, p):
        p.add_argument(
            "--raw", default="data/raw",
            help="Path to the data/raw directory written by fetch_open_data.py (default: data/raw)",
        )
        p.add_argument(
            "--only", nargs="+",
            choices=["timeseries", "static", "climate", "glw4"],
            help="Limit to specific dataset groups (default: all)",
        )
        p.add_argument(
            "--skip-derive", action="store_true",
            help="Do not re-derive VCI / SPI / anomaly indicators after ingesting",
        )

    def handle(self, *args, **opts):
        raw_dir = Path(opts["raw"])
        if not raw_dir.is_dir():
            raise CommandError(
                f"Raw data directory not found: {raw_dir.resolve()}\n"
                "Run fetch_open_data.py first, or pass --raw <path>."
            )

        todo = set(opts["only"] or ["timeseries", "static", "climate", "glw4"])
        log = self.stdout.write

        run = IngestRun.objects.create(
            command="ingest_open_data",
            params={"raw": str(raw_dir), "only": list(todo)},
        )
        written = 0

        try:
            ts_dir = raw_dir / "timeseries"
            st_dir = raw_dir / "static"

            # ---- time series ----
            if "timeseries" in todo:
                log("── Time series ─────────────────────────────────────────")
                for fname, loader in [
                    ("chirps_monthly_mm.csv", load_chirps),
                    ("modis_ndvi_terra_16day.csv", load_ndvi),
                    ("modis_lst_8day.csv", load_lst),
                    ("modis_npp_annual.csv", load_modis_npp),
                    ("wapor_npp_dekadal_v2_raw.csv", load_wapor_npp),
                    ("era5land_monthly.csv", load_era5),
                    ("nasa_power_monthly_ward_centroids.csv", load_nasa_power),
                ]:
                    p = ts_dir / fname
                    if not p.exists():
                        log(f"  {fname}: not found – skipping")
                        continue
                    try:
                        written += loader(p, log)
                    except Exception as e:
                        log(self.style.WARNING(f"  {fname}: FAILED – {e}"))

            # ---- static ward stats ----
            if "static" in todo:
                log("── Static ward statistics ──────────────────────────────")
                for fname, loader in [
                    ("worldpop_ward_population_2000_2020.csv", load_worldpop),
                    ("ward_static_water_land_terrain.csv", load_ward_static),
                ]:
                    p = st_dir / fname
                    if not p.exists():
                        log(f"  {fname}: not found – skipping")
                        continue
                    try:
                        written += loader(p, log)
                    except Exception as e:
                        log(self.style.WARNING(f"  {fname}: FAILED – {e}"))

            # ---- GLW4 rasters ----
            if "glw4" in todo:
                log("── GLW4 livestock rasters ──────────────────────────────")
                try:
                    written += load_glw4(raw_dir, log)
                except Exception as e:
                    log(self.style.WARNING(f"  glw4: FAILED – {e}"))

            # ---- climate driver indices ----
            if "climate" in todo:
                log("── Climate driver indices ───────────────────────────────")
                try:
                    written += load_climate_indices(raw_dir, log)
                except Exception as e:
                    log(self.style.WARNING(f"  climate_drivers: FAILED – {e}"))

            # ---- re-derive VCI / SPI ----
            if not opts["skip_derive"] and "timeseries" in todo:
                log("── Deriving VCI / SPI indicators ───────────────────────")
                try:
                    from ...indicator_logic import derive
                    n_derived = derive()
                    log(f"  derived {n_derived} rows (vci, vci3, ndvi_anom, rain_pon3, rain_spi3)")
                    written += n_derived
                except Exception as e:
                    log(self.style.WARNING(f"  derive: FAILED – {e}"))

            run.status = "ok"
            log(self.style.SUCCESS(f"\nDone.  {written} total rows written / updated."))

        except Exception as exc:
            run.status = "failed"
            run.notes = str(exc)[:500]
            raise
        finally:
            run.rows_written = written
            run.finished_at = timezone.now()
            run.save()
