"""Ward-level NDVI and rainfall indicators, no Earth Engine, no HDF4.

    python manage.py update_indicators            # incremental, safe to re-run / cron weekly
    python manage.py update_indicators --skip-rain

NDVI: MODIS MOD13Q1 v061 COGs from the Microsoft Planetary Computer STAC (free, anonymous signing).
Rain: CHIRPS v2.0 monthly Africa GeoTIFFs from the Climate Hazards Center (free, no login).
Verify the CHIRPS URL pattern by opening https://data.chc.ucsb.edu/products/CHIRPS-2.0/ in a browser.
"""
import datetime as dt
import gzip
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Max
from pyproj import Transformer
from rasterio import features, windows
from rasterio.io import MemoryFile
from rasterio.mask import mask as rio_mask
from scipy import stats
from shapely import wkt
from shapely.geometry import mapping
from shapely.ops import transform as shp_transform

from ...models import WardIndicator
from ...views import _layers, _q

METH = "1.0"
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
CHIRPS = "https://data.chc.ucsb.edu/products/CHIRPS-2.0/africa_monthly/tifs/chirps-v2.0.{y}.{m:02d}.tif.gz"
MIN_VALID = 0.5                 # publish a ward NDVI only if >= 50% of its pixels are valid
VCI_BASE = (2001, 2025)         # baseline years for VCI / anomaly
RAIN_BASE = (1991, 2020)        # baseline years for percent-of-normal and SPI


def load_wards():
    c = _layers()["wards"]
    g, code = _q(c["geom"]), _q(c["code"])
    sql = f"SELECT {code}::text, ST_AsText(ST_Transform({g}, 4326)) FROM public.{_q(c['table'])} WHERE {g} IS NOT NULL"
    with connection.cursor() as cur:
        cur.execute(sql)
        return [(r[0], wkt.loads(r[1])) for r in cur.fetchall()]


def save(rows):
    objs = [WardIndicator(methodology_version=METH, **r) for r in rows]
    WardIndicator.objects.bulk_create(
        objs, batch_size=2000, update_conflicts=True,
        unique_fields=["ward_code", "indicator", "period_start"],
        update_fields=["period_end", "value", "valid_fraction", "source", "methodology_version"])


def clean(v):
    return None if v is None or pd.isna(v) else float(v)


# ---------------------------------------------------------------- NDVI
def zonal_item(item, wards):
    """Read the county window once per item, then reduce per ward with a label raster."""
    a = item.assets
    with rasterio.open(a["250m_16_days_NDVI"].href) as src:
        to_src = Transformer.from_crs(4326, src.crs, always_xy=True).transform
        geoms = [shp_transform(to_src, g) for _, g in wards]
        b = (min(g.bounds[0] for g in geoms), min(g.bounds[1] for g in geoms),
             max(g.bounds[2] for g in geoms), max(g.bounds[3] for g in geoms))
        win = windows.from_bounds(*b, transform=src.transform).round_offsets().round_lengths()
        try:
            win = windows.intersection(win, windows.Window(0, 0, src.width, src.height))
        except windows.WindowError:
            return None
        nd, tf = src.read(1, window=win), src.window_transform(win)
    if "250m_16_days_pixel_reliability" in a:
        with rasterio.open(a["250m_16_days_pixel_reliability"].href) as s2:
            qa = s2.read(1, window=win)
        ok = (qa == 0) | (qa == 1)                    # good or marginal
    else:
        with rasterio.open(a["250m_16_days_VI_Quality"].href) as s2:
            qa = s2.read(1, window=win)
        ok = (qa & 3) <= 1
    lab = features.rasterize([(g, i + 1) for i, g in enumerate(geoms)], out_shape=nd.shape,
                             transform=tf, fill=0, dtype="int32")
    inw = lab > 0
    good = inw & ok & (nd >= -2000)
    n = len(wards) + 1
    return (np.bincount(lab[good], weights=nd[good] * 1e-4, minlength=n),
            np.bincount(lab[good], minlength=n), np.bincount(lab[inw], minlength=n))


def ingest_ndvi(wards, since):
    import planetary_computer as pc
    from pystac_client import Client
    last = WardIndicator.objects.filter(indicator="ndvi").aggregate(m=Max("period_start"))["m"]
    start = max(since, last - dt.timedelta(days=16)) if last else since      # re-read last composite
    lons = [x for _, g in wards for x in (g.bounds[0], g.bounds[2])]
    lats = [y for _, g in wards for y in (g.bounds[1], g.bounds[3])]
    cat = Client.open(STAC, modifier=pc.sign_inplace)
    items = cat.search(collections=["modis-13Q1-061"], bbox=[min(lons), min(lats), max(lons), max(lats)],
                       datetime=f"{start}/{dt.date.today()}").item_collection()
    acc = {}
    for it in items:
        z = zonal_item(it, wards)
        if z is None:
            continue
        d = pd.Timestamp(it.properties.get("start_datetime") or it.datetime).date()
        cur = acc.setdefault(d, [np.zeros_like(z[0]), np.zeros_like(z[1]), np.zeros_like(z[2])])
        for k in range(3):
            cur[k] += z[k]
    rows = []
    for d, (sm, cnt, tot) in acc.items():
        for i, (code, _) in enumerate(wards, start=1):
            frac = cnt[i] / tot[i] if tot[i] else 0
            rows.append(dict(ward_code=code, indicator="ndvi", period_start=d, period_end=d + dt.timedelta(days=15),
                             value=float(sm[i] / cnt[i]) if frac >= MIN_VALID else None,
                             valid_fraction=float(frac), source="modis-13Q1-061"))
    save(rows)
    return len(rows)


# ---------------------------------------------------------------- Rain
def ingest_rain(wards, since):
    cache = Path(settings.BASE_DIR) / "data" / "raw" / "chirps"
    cache.mkdir(parents=True, exist_ok=True)
    last = WardIndicator.objects.filter(indicator="rain_mm").aggregate(m=Max("period_start"))["m"]
    y, m = (last.year, last.month) if last else (since.year, since.month)
    if last:                                              # re-fetch the last 3 months (values can be revised)
        m -= 3
        while m < 1:
            y, m = y - 1, m + 12
    rows, today = [], dt.date.today()
    while dt.date(y, m, 1) <= today:
        f = cache / f"chirps-v2.0.{y}.{m:02d}.tif.gz"
        if not f.exists() or f.stat().st_mtime < (dt.datetime.now() - dt.timedelta(days=30)).timestamp() and dt.date(y, m, 1) > today - dt.timedelta(days=120):
            r = requests.get(CHIRPS.format(y=y, m=m), timeout=120)
            if r.status_code == 404:
                break                                     # month not published yet
            r.raise_for_status()
            f.write_bytes(r.content)
        with MemoryFile(gzip.decompress(f.read_bytes())) as mf, mf.open() as src:
            start = dt.date(y, m, 1)
            end = (pd.Timestamp(start) + pd.offsets.MonthEnd(0)).date()
            for code, g in wards:
                out, _ = rio_mask(src, [mapping(g)], crop=True, all_touched=True, filled=False)
                vals = out.compressed() if hasattr(out, "compressed") else out.ravel()
                vals = vals[vals >= 0]
                rows.append(dict(ward_code=code, indicator="rain_mm", period_start=start, period_end=end,
                                 value=float(vals.mean()) if vals.size else None,
                                 valid_fraction=None, source="chirps-v2.0"))
        y, m = (y, m + 1) if m < 12 else (y + 1, 1)
    save(rows)
    return len(rows)


# ---------------------------------------------------------------- Derived
def derive():
    df = pd.DataFrame(WardIndicator.objects.filter(indicator__in=["ndvi", "rain_mm"])
                      .values("ward_code", "indicator", "period_start", "value"))
    if df.empty:
        return 0
    df["period_start"] = pd.to_datetime(df["period_start"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["year"], out = df.period_start.dt.year, []

    nd = df[df.indicator == "ndvi"].copy()
    if not nd.empty:
        nd["doy"] = nd.period_start.dt.dayofyear
        base = nd[nd.year.between(*VCI_BASE)].groupby(["ward_code", "doy"])["value"].agg(["min", "max", "mean"]).reset_index()
        nd = nd.merge(base, on=["ward_code", "doy"], how="left").sort_values(["ward_code", "period_start"])
        rng = nd["max"] - nd["min"]
        nd["vci"] = np.where(rng > 0.01, 100 * (nd["value"] - nd["min"]) / rng, np.nan)
        nd["ndvi_anom"] = nd["value"] - nd["mean"]
        nd["vci3"] = nd.groupby("ward_code")["vci"].transform(lambda s: s.rolling(6, min_periods=4).mean())
        for ind in ("vci", "vci3", "ndvi_anom"):
            out += [dict(ward_code=r.ward_code, indicator=ind, period_start=r.period_start.date(), value=clean(getattr(r, ind)),
                         source="derived") for r in nd.itertuples()]

    rn = df[df.indicator == "rain_mm"].copy().sort_values(["ward_code", "period_start"])
    if not rn.empty:
        rn["month"] = rn.period_start.dt.month
        rn["r3"] = rn.groupby("ward_code")["value"].transform(lambda s: s.rolling(3, min_periods=3).sum())
        rn["pon3"], rn["spi3"] = np.nan, np.nan
        for (w, mth), grp in rn.groupby(["ward_code", "month"]):
            b = grp[grp.year.between(*RAIN_BASE)]["r3"].dropna()
            if len(b) < 15:
                continue
            rn.loc[grp.index, "pon3"] = 100 * grp["r3"] / b.mean() if b.mean() > 0 else np.nan
            pos = b[b > 0]
            if len(pos) >= 10:
                q = 1 - len(pos) / len(b)
                a, _, sc = stats.gamma.fit(pos, floc=0)
                h = q + (1 - q) * stats.gamma.cdf(grp["r3"], a, loc=0, scale=sc)
                rn.loc[grp.index, "spi3"] = stats.norm.ppf(np.clip(h, 1e-6, 1 - 1e-6))
        for ind, col in (("rain_pon3", "pon3"), ("rain_spi3", "spi3")):
            out += [dict(ward_code=r.ward_code, indicator=ind, period_start=r.period_start.date(), value=clean(getattr(r, col)),
                         source="derived") for r in rn.itertuples()]
    save(out)
    return len(out)


class Command(BaseCommand):
    help = "Update ward-level NDVI, rainfall and derived indicators (incremental)."

    def add_arguments(self, p):
        p.add_argument("--ndvi-since", default="2001-01-01")
        p.add_argument("--rain-since", default="1991-01-01")
        p.add_argument("--skip-ndvi", action="store_true")
        p.add_argument("--skip-rain", action="store_true")

    def handle(self, *a, **o):
        wards = load_wards()
        self.stdout.write(f"{len(wards)} wards loaded")
        if not o["skip_ndvi"]:
            self.stdout.write(f"NDVI rows: {ingest_ndvi(wards, dt.date.fromisoformat(o['ndvi_since']))}")
        if not o["skip_rain"]:
            self.stdout.write(f"Rain rows: {ingest_rain(wards, dt.date.fromisoformat(o['rain_since']))}")
        self.stdout.write(f"Derived rows: {derive()}")