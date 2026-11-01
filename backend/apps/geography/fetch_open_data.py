#!/usr/bin/env python3
"""Fetch free, open datasets for the Terra Range pilot into data/raw/, under a hard size budget.

No Django needed. Every dataset is optional and fails soft (a failed source never stops the others).
Time series go back as far as each source allows; static layers are the latest version only.
Earth Engine datasets are reduced to ward level inside Earth Engine, so only small CSVs come back.

    python scripts/fetch_open_data.py --wards data/wards.geojson --list
    python scripts/fetch_open_data.py --wards data/wards.geojson            # everything possible
    python scripts/fetch_open_data.py --wards data/wards.geojson --only chirps_monthly nasa_power

Export wards first (code column must match your database):
    ogr2ogr -f GeoJSON data/wards.geojson /path/terra_range_admin_final.gpkg wards -t_srs EPSG:4326

Earth Engine datasets need:  pip install earthengine-api ; earthengine authenticate ; export EE_PROJECT=<project>
Optional extras: rasterio (GLW clipping), pdfplumber (census table text), shapely (smaller ward polygons).
Budget: --budget-mb counts everything kept under --out (default 90 MB). --max-transfer-mb caps one download.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

MB = 1024 * 1024
TODAY = dt.date.today()
HEADERS = {"User-Agent": f"TerraRange-open-data/0.1 ({os.environ.get('TERRA_CONTACT', 'research use')})"}
COUNTIES = {"Marsabit", "Isiolo", "Samburu", "Turkana", "Wajir", "Mandera"}   # price markets kept (Marsabit + neighbours)


class Skip(Exception):
    """An expected, non-fatal reason a dataset was not fetched."""


# ----------------------------------------------------------------------------- store with a size budget
class Store:
    def __init__(self, root, budget_mb=90.0, max_file_mb=40.0):
        self.root, self.budget, self.max_file = Path(root), int(budget_mb * MB), int(max_file_mb * MB)
        self.root.mkdir(parents=True, exist_ok=True)
        self.sources = self.root / "SOURCES.csv"
        if not self.sources.exists():
            self.sources.write_text("file,dataset,provider,url,licence,period,resolution,retrieved,bytes,sha256,notes\n")

    def used(self):
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())

    def room(self, replacing=None):
        return self.budget - self.used() + (replacing.stat().st_size if replacing and replacing.exists() else 0)

    def exists(self, rel):
        return (self.root / rel).exists()

    def write(self, rel, content, meta):
        b = content.encode("utf-8") if isinstance(content, str) else content
        p = self.root / rel
        limit = min(self.room(p), self.max_file)
        if len(b) > limit:
            raise Skip(f"{rel} is {len(b) / MB:.1f} MB, over the {limit / MB:.1f} MB still available")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b)
        with self.sources.open("a", newline="") as f:
            csv.writer(f).writerow([rel, meta.get("dataset", ""), meta.get("provider", ""), meta.get("url", ""),
                                    meta.get("licence", ""), meta.get("period", ""), meta.get("resolution", ""),
                                    TODAY.isoformat(), len(b), hashlib.sha256(b).hexdigest(), meta.get("notes", "")])
        return p


def get_bytes(url, cap, params=None, timeout=180):
    """Stream a download into memory, giving up as soon as it would exceed `cap` bytes."""
    with requests.get(url, params=params, headers=HEADERS, stream=True, timeout=timeout, allow_redirects=True) as r:
        r.raise_for_status()
        size = int(r.headers.get("Content-Length") or 0)
        if size > cap:
            raise Skip(f"{size / MB:.0f} MB is larger than the {cap / MB:.0f} MB transfer cap")
        buf, total = io.BytesIO(), 0
        for chunk in r.iter_content(256 * 1024):
            total += len(chunk)
            if total > cap:
                raise Skip(f"download passed the {cap / MB:.0f} MB transfer cap")
            buf.write(chunk)
        return buf.getvalue()


def to_csv(rows, columns):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(columns)
    for r in rows:
        w.writerow(["" if r.get(c) is None else r.get(c) for c in columns])
    return out.getvalue()


# ----------------------------------------------------------------------------- wards and context
def _coords(g):
    def walk(c):
        if c and isinstance(c[0], (int, float)):
            yield c
        else:
            for x in c:
                yield from walk(x)
    return list(walk(g["coordinates"]))


def load_wards(path, code_field=None):
    feats = json.load(open(path, encoding="utf-8"))["features"]
    props = feats[0]["properties"]
    field = next((k for k in ([code_field] if code_field else ["code", "ward_code", "WARD_CODE", "Code", "id", "ID", "uid"])
                  if k in props), None) or next((k for k, v in props.items() if isinstance(v, str)), None)
    if field is None:
        raise SystemExit("Could not find a ward code column; pass --code-field")
    try:                                              # smaller polygons make Earth Engine requests lighter
        from shapely.geometry import mapping, shape
        geoms = [mapping(shape(f["geometry"]).simplify(0.0003, preserve_topology=True)) for f in feats]
    except ImportError:
        geoms = [f["geometry"] for f in feats]
    return [(str(f["properties"][field]), g) for f, g in zip(feats, geoms)], field


class Ctx:
    def __init__(self, args, store, wards):
        self.args, self.store, self.wards = args, store, wards
        pts = [c for _, g in wards for c in _coords(g)]
        self.bbox = (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))
        self.centroids = []
        for code, g in wards:
            p = _coords(g)
            self.centroids.append((code, (sum(x[0] for x in p) / len(p), sum(x[1] for x in p) / len(p))))
        self._ee = self._fc = None

    def pad_bbox(self, d=0.3):
        w, s, e, n = self.bbox
        return w - d, s - d, e + d, n + d

    def ee(self):
        if self._ee is None:
            try:
                import ee
            except ImportError:
                raise Skip("pip install earthengine-api")
            if not os.environ.get("EE_PROJECT"):
                raise Skip("set EE_PROJECT to your Earth Engine Cloud project id")
            try:
                ee.Initialize(project=os.environ["EE_PROJECT"])
            except Exception as e:
                raise Skip(f"Earth Engine not ready ({e}); run: earthengine authenticate")
            self._ee = ee
        return self._ee

    def fc(self):
        if self._fc is None:
            ee = self.ee()
            self._fc = ee.FeatureCollection([ee.Feature(ee.Geometry(g, None, False), {"code": c}) for c, g in self.wards])
        return self._fc

    def skip_if_have(self, rel):
        if self.store.exists(rel) and not self.args.refresh:
            raise Skip(f"{rel} already present (use --refresh)")


def ee_get(obj, tries=4):
    for i in range(tries):
        try:
            return obj.getInfo()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


# ----------------------------------------------------------------------------- Earth Engine time series
def ee_series(ctx, rel, make, y0, y1, scale, cols, meta):
    ctx.skip_if_have(rel)
    ee, fc = ctx.ee(), ctx.fc()

    def one(y):
        col = make(ee, y)
        rows = col.map(lambda img: img.reduceRegions(collection=fc, reducer=ee.Reducer.mean(), scale=scale, tileScale=4)
                       .map(lambda f: f.set("date", img.get("date")).setGeometry(None))).flatten()
        return [x["properties"] for x in ee_get(rows)["features"]]
    with ThreadPoolExecutor(ctx.args.workers) as ex:
        chunks = list(ex.map(one, range(y0, y1 + 1)))
    rows = sorted((r for c in chunks for r in c), key=lambda r: (r["code"], r["date"]))
    ctx.store.write(rel, to_csv(rows, ["code", "date"] + cols), meta)
    return f"{len(rows)} ward-periods, {y0}-{y1}"


def _date(i):
    return i.date().format("YYYY-MM-dd")


def mk_ndvi(ee, y):
    def prep(i):
        good = i.select("SummaryQA").lte(1)
        return (i.select(["NDVI", "EVI"]).multiply(0.0001).updateMask(good).rename(["ndvi", "evi"])
                .addBands(good.rename("valid")).set("date", _date(i)))
    return ee.ImageCollection("MODIS/061/MOD13Q1").filterDate(f"{y}-01-01", f"{y + 1}-01-01").map(prep)


def mk_chirps(ee, y):
    last = 11 if y < TODAY.year else TODAY.month - 2          # skip the current, incomplete month
    if last < 0:
        return ee.ImageCollection([])
    ch = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")

    def month(n):
        s = ee.Date.fromYMD(y, 1, 1).advance(n, "month")
        return ch.filterDate(s, s.advance(1, "month")).sum().rename("rain_mm").set("date", s.format("YYYY-MM-dd"))
    return ee.ImageCollection(ee.List.sequence(0, last).map(month))


ERA5 = ["temperature_2m", "total_precipitation_sum", "volumetric_soil_water_layer_1",
        "volumetric_soil_water_layer_2", "potential_evaporation_sum"]
EE_SERIES = {   # name: (file, image builder, first year, last year, scale m, columns, meta)
    "modis_ndvi": ("modis_ndvi_terra_16day.csv", mk_ndvi, 2000, TODAY.year, 250, ["ndvi", "evi", "valid"],
                   dict(dataset="MODIS MOD13Q1 v061 NDVI/EVI, ward mean", provider="NASA LP DAAC via Earth Engine",
                        url="MODIS/061/MOD13Q1", licence="NASA open data", period="2000-", resolution="250 m, 16-day",
                        notes="Terra only; SummaryQA<=1; 'valid' = valid-pixel fraction")),
    "chirps_monthly": ("chirps_monthly_mm.csv", mk_chirps, 1981, TODAY.year, 5566, ["rain_mm"],
                       dict(dataset="CHIRPS v2 monthly rainfall, ward mean", provider="UCSB CHC via Earth Engine",
                            url="UCSB-CHG/CHIRPS/DAILY", licence="CC0 / public domain", period="1981-", resolution="0.05 deg")),
    "era5land_monthly": ("era5land_monthly.csv", lambda ee, y: ee.ImageCollection("ECMWF/ERA5_LAND/MONTHLY_AGGR")
                         .filterDate(f"{y}-01-01", f"{y + 1}-01-01").select(ERA5).map(lambda i: i.set("date", _date(i))),
                         1950, TODAY.year, 11132, ERA5,
                         dict(dataset="ERA5-Land monthly (temperature K, rain/PET m, soil water m3/m3)", provider="ECMWF/Copernicus via Earth Engine",
                              url="ECMWF/ERA5_LAND/MONTHLY_AGGR", licence="Copernicus licence (free, attribution)", period="1950-", resolution="0.1 deg")),
    "modis_lst": ("modis_lst_8day.csv", lambda ee, y: ee.ImageCollection("MODIS/061/MOD11A2").filterDate(f"{y}-01-01", f"{y + 1}-01-01")
                  .map(lambda i: i.select(["LST_Day_1km", "LST_Night_1km"]).multiply(0.02).rename(["lst_day_k", "lst_night_k"]).set("date", _date(i))),
                  2000, TODAY.year, 1000, ["lst_day_k", "lst_night_k"],
                  dict(dataset="MODIS MOD11A2 land surface temperature (K)", provider="NASA via Earth Engine", url="MODIS/061/MOD11A2",
                       licence="NASA open data", period="2000-", resolution="1 km, 8-day")),
    "modis_npp_annual": ("modis_npp_annual.csv", lambda ee, y: ee.ImageCollection("MODIS/061/MOD17A3HGF").filterDate(f"{y}-01-01", f"{y + 1}-01-01")
                         .map(lambda i: i.select("Npp").multiply(0.0001).rename("npp_kgc_m2").set("date", _date(i))),
                         2001, TODAY.year - 1, 500, ["npp_kgc_m2"],
                         dict(dataset="MODIS MOD17A3HGF annual NPP (kgC/m2/yr)", provider="NASA via Earth Engine", url="MODIS/061/MOD17A3HGF",
                              licence="NASA open data", period="2001-", resolution="500 m, annual", notes="forage productivity proxy")),
    "wapor_npp_v2": ("wapor_npp_dekadal_v2_raw.csv", lambda ee, y: ee.ImageCollection("FAO/WAPOR/2/L1_NPP_D").filterDate(f"{y}-01-01", f"{y + 1}-01-01")
                     .map(lambda i: i.select("L1_NPP_D").rename("npp_dn").set("date", _date(i))),
                     2009, 2023, 250, ["npp_dn"],
                     dict(dataset="WaPOR v2 L1 dekadal NPP (raw digital numbers; apply FAO scale factors)", provider="FAO via Earth Engine",
                          url="FAO/WAPOR/2/L1_NPP_D", licence="FAO WaPOR (check NC terms)", period="2009-2023-03", resolution="250 m, dekadal",
                          notes="v2 ends in March 2023; v3 not fetched here")),
}


def run_ee_series(name):
    rel, make, y0, y1, scale, cols, meta = EE_SERIES[name]
    return lambda ctx: ee_series(ctx, f"timeseries/{rel}", make, y0, y1, scale, cols, meta)


# ----------------------------------------------------------------------------- Earth Engine static layers
def ee_worldpop(ctx):
    rel = "static/worldpop_ward_population_2000_2020.csv"
    ctx.skip_if_have(rel)
    ee, fc = ctx.ee(), ctx.fc()

    def one(y):
        img = (ee.ImageCollection("WorldPop/GP/100m/pop").filter(ee.Filter.eq("country", "KEN"))
               .filter(ee.Filter.eq("year", y)).first())
        out = ee_get(img.reduceRegions(collection=fc, reducer=ee.Reducer.sum(), scale=100, tileScale=4).select(["code", "sum"]))
        return [{"code": f["properties"]["code"], "year": y, "population": f["properties"].get("sum")} for f in out["features"]]
    with ThreadPoolExecutor(ctx.args.workers) as ex:
        rows = [r for c in ex.map(one, range(2000, 2021)) for r in c]
    ctx.store.write(rel, to_csv(sorted(rows, key=lambda r: (r["code"], r["year"])), ["code", "year", "population"]),
                    dict(dataset="WorldPop yearly population by ward (unconstrained, UN-adjusted)", provider="WorldPop via Earth Engine",
                         url="WorldPop/GP/100m/pop", licence="CC BY 4.0", period="2000-2020", resolution="100 m"))
    return f"{len(rows)} ward-years"


def ee_static(ctx):
    rel = "static/ward_static_water_land_terrain.csv"
    ctx.skip_if_have(rel)
    ee, fc = ctx.ee(), ctx.fc()  # fc builds the ward collection once
    gsw = ee.Image("JRC/GSW1_4/GlobalSurfaceWater")
    km2, seas = ee.Image.pixelArea().divide(1e6), gsw.select("seasonality")
    water = ee.Image.cat([km2.rename("ward_area_km2"), km2.updateMask(seas.eq(12)).rename("water_permanent_km2"),
                          km2.updateMask(seas.gt(0).And(seas.lt(12))).rename("water_seasonal_km2"),
                          km2.updateMask(gsw.select("max_extent").eq(1)).rename("water_max_extent_km2")])
    srtm = ee.Image("USGS/SRTMGL1_003")
    terrain = ee.Image.cat([srtm.select("elevation"), ee.Terrain.slope(srtm).rename("slope")])
    cover = ee.Image("ESA/WorldCover/v200/2021").select("Map")
    rd = ee.Reducer.mean().combine(ee.Reducer.min(), sharedInputs=True).combine(ee.Reducer.max(), sharedInputs=True)

    def one(item):
        code, g = item
        geom = ee.Geometry(g, None, False)
        w = ee_get(water.reduceRegion(reducer=ee.Reducer.sum(), geometry=geom, scale=30, maxPixels=1e10, tileScale=8))
        t = ee_get(terrain.reduceRegion(reducer=rd, geometry=geom, scale=90, maxPixels=1e10, tileScale=4))
        h = ee_get(cover.reduceRegion(reducer=ee.Reducer.frequencyHistogram(), geometry=geom, scale=100, maxPixels=1e10, tileScale=4))
        hist = h.get("Map") or {}
        tot = sum(hist.values()) or 1
        row = {"code": code, **w, **t}
        row.update({f"worldcover_{k}_pct": round(100 * v / tot, 2) for k, v in hist.items()})
        return row
    with ThreadPoolExecutor(ctx.args.workers) as ex:
        rows = list(ex.map(one, ctx.wards))
    cols = sorted({k for r in rows for k in r} - {"code"})
    ctx.store.write(rel, to_csv(rows, ["code"] + cols),
                    dict(dataset="Ward statics: JRC surface-water areas, SRTM elevation/slope, ESA WorldCover % by class",
                         provider="JRC / NASA / ESA via Earth Engine", url="JRC/GSW1_4, USGS/SRTMGL1_003, ESA/WorldCover/v200",
                         licence="CC BY 4.0 (WorldCover, JRC) / public domain (SRTM)", period="latest", resolution="30-100 m",
                         notes="WorldCover class codes: 10 tree, 20 shrub, 30 grass, 40 crop, 50 built, 60 bare, 80 water"))
    return f"{len(rows)} wards"


# ----------------------------------------------------------------------------- web sources (no login)
def fetch_nasa_power(ctx):
    rel = "timeseries/nasa_power_monthly_ward_centroids.csv"
    ctx.skip_if_have(rel)
    params = "T2M,T2M_MAX,T2M_MIN,PRECTOTCORR,RH2M,ALLSKY_SFC_SW_DWN,WS2M"
    rows = []
    for code, (lon, lat) in ctx.centroids:
        r = requests.get("https://power.larc.nasa.gov/api/temporal/monthly/point", headers=HEADERS, timeout=120,
                         params=dict(parameters=params, community="AG", longitude=round(lon, 3), latitude=round(lat, 3),
                                     start=1981, end=TODAY.year, format="JSON"))
        r.raise_for_status()
        par = r.json()["properties"]["parameter"]
        for m in sorted({k for v in par.values() for k in v if not k.endswith("13")}):
            row = {"code": code, "month": f"{m[:4]}-{m[4:]}"}
            for p, series in par.items():
                v = series.get(m)
                row[p] = None if v is None or v <= -990 else v
            rows.append(row)
    ctx.store.write(rel, to_csv(rows, ["code", "month"] + params.split(",")),
                    dict(dataset="NASA POWER monthly meteorology at ward centroids", provider="NASA POWER",
                         url="https://power.larc.nasa.gov/api/temporal/monthly/point", licence="NASA open data",
                         period="1981-", resolution="~50 km model grid", notes="values -999 = missing; rain in mm/day; model data, not stations"))
    return f"{len(rows)} ward-months"


INDICES = {"oni_cpc.txt": "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt",
           "nino34_anom_psl.data": "https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/nino34.long.anom.data",
           "dmi_hadisst_psl.data": "https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/dmi.had.long.data"}


def fetch_indices(ctx):
    n = 0
    for name, url in INDICES.items():
        if ctx.store.exists(f"climate_drivers/{name}") and not ctx.args.refresh:
            continue
        ctx.store.write(f"climate_drivers/{name}", get_bytes(url, 5 * MB),
                        dict(dataset=f"Climate driver index ({name})", provider="NOAA CPC / PSL", url=url,
                             licence="US public domain", period="long record", resolution="monthly/seasonal",
                             notes="ENSO (ONI, Nino3.4) and Indian Ocean Dipole (DMI); IOD matters for OND rains"))
        n += 1
    if not n:
        raise Skip("already present")
    return f"{n} files"


def hdx_find(query, must):
    r = requests.get("https://data.humdata.org/api/3/action/package_search", params={"q": query, "rows": 15}, headers=HEADERS, timeout=60)
    r.raise_for_status()
    for p in r.json()["result"]["results"]:
        if all(m in p["title"].lower() for m in must):
            return p
    raise Skip(f"no HDX dataset matched {must}")


def fetch_wfp_prices(ctx):
    rel = "timeseries/wfp_food_prices_kenya_north.csv"
    ctx.skip_if_have(rel)
    pkg = hdx_find("wfp food prices kenya", ["food prices", "kenya"])
    res = next((x for x in pkg["resources"] if x["format"].lower() == "csv" and "qc" not in x["name"].lower()), None)
    if not res:
        raise Skip("no CSV resource on the HDX dataset")
    rows = list(csv.reader(io.StringIO(get_bytes(res["url"], ctx.args.max_transfer).decode("utf-8-sig"))))
    head = rows[0]
    a1 = head.index("admin1") if "admin1" in head else None
    keep = [rows[0]] + [r for r in rows[1:] if r and (r[0].startswith("#") or a1 is None or r[a1] in COUNTIES)]
    out = io.StringIO()
    csv.writer(out).writerows(keep)
    ctx.store.write(rel, out.getvalue(), dict(dataset="WFP food prices, Kenya (Marsabit and neighbouring counties)", provider="WFP via HDX",
                                              url=res["url"], licence="CC BY-IGO (check HDX page)", period="2006-", resolution="market, monthly",
                                              notes="goat/cattle/camel and maize prices; terms-of-trade input"))
    return f"{len(keep) - 1} price rows"


def fetch_icpac_water(ctx):
    rel = "static/icpac_ken_waterpoints1.geojson"
    ctx.skip_if_have(rel)
    w, s, e, n = ctx.pad_bbox(0.05)
    data = get_bytes("https://geoportal.icpac.net/geoserver/ows", ctx.args.max_transfer,
                     params=dict(service="WFS", version="1.0.0", request="GetFeature", typeName="geonode:ken_waterpoints1",
                                 outputFormat="application/json", bbox=f"{w},{s},{e},{n},EPSG:4326"))
    if not data.lstrip().startswith(b"{"):
        raise Skip("ICPAC did not return GeoJSON; download the layer manually from geoportal.icpac.net")
    ctx.store.write(rel, data, dict(dataset="Kenya water points (dams, springs, wells, water holes)", provider="ICPAC geoportal (ACT database)",
                                    url="geoportal.icpac.net layer ken_waterpoints1", licence="check layer page", period="static",
                                    resolution="point", notes="inventory, not status; clipped to the county bounding box"))
    return f"{len(json.loads(data)['features'])} points"


def fetch_wpdx(ctx):
    rel = "static/wpdx_marsabit_area.csv"
    ctx.skip_if_have(rel)
    host = "https://data.waterpointdata.org"
    cat = requests.get("https://api.us.socrata.com/api/catalog/v1", headers=HEADERS, timeout=60,
                       params={"domains": "data.waterpointdata.org", "q": "WPdx", "only": "datasets", "limit": 10}).json()["results"]
    pick = next((c for c in cat if "basic" in c["resource"]["name"].lower()), None) or (cat[0] if cat else None)
    if not pick:
        raise Skip("WPdx dataset not found in the Socrata catalogue")
    rid = pick["resource"]["id"]
    sample = requests.get(f"{host}/resource/{rid}.json", params={"$limit": 1}, headers=HEADERS, timeout=60).json()
    lat = next((k for k in sample[0] if k.endswith("lat_deg")), None)
    lon = next((k for k in sample[0] if k.endswith("lon_deg")), None)
    if not (lat and lon):
        raise Skip("could not find latitude/longitude columns in WPdx")
    w, s, e, n = ctx.pad_bbox(0.1)
    data = get_bytes(f"{host}/resource/{rid}.csv", ctx.args.max_transfer,
                     params={"$where": f"{lat} between {s} and {n} AND {lon} between {w} and {e}", "$limit": 50000})
    ctx.store.write(rel, data, dict(dataset=f"WPdx water points in/near the county ({pick['resource']['name']})", provider="Water Point Data Exchange",
                                    url=f"{host}/resource/{rid}", licence="check WPdx terms (open data)", period="varies by record",
                                    resolution="point", notes="status fields are reported, not verified"))
    n_rows = max(data.count(b"\n") - 1, 0)
    return f"{n_rows} rows"


def fetch_fews_lhz(ctx):
    rel_dir = "static/fewsnet_livelihood_zones"
    if (ctx.store.root / rel_dir).exists() and not ctx.args.refresh:
        raise Skip("already present")
    z = zipfile.ZipFile(io.BytesIO(get_bytes("http://shapefiles.fews.net.s3.amazonaws.com/LHZ/FEWS_NET_LH_World.zip", ctx.args.max_transfer)))
    names = [n for n in z.namelist() if re.search(r"(^|/)(ke|ken|kenya)[_\-. ]", n.lower())]
    if not names:
        raise Skip(f"no Kenya files found in the archive ({len(z.namelist())} entries, e.g. {z.namelist()[:4]})")
    for n in names:
        ctx.store.write(f"{rel_dir}/{Path(n).name}", z.read(n),
                        dict(dataset="FEWS NET livelihood zones, Kenya", provider="FEWS NET", url="fews.net/data/livelihood-zones",
                             licence="not specified: keep FEWS NET attribution", period="latest zoning", resolution="polygons"))
    return f"{len(names)} files"


def fetch_glw4(ctx):
    try:
        from rasterio.io import MemoryFile
        from rasterio.windows import from_bounds
    except ImportError:
        raise Skip("pip install rasterio")
    base, got = "https://dataverse.harvard.edu/api", []
    for stem, label in (("Ct", "cattle"), ("Gt", "goats"), ("Sh", "sheep")):
        rel = f"static/glw4_{label}_2015_dasymetric_kenya.tif"
        if ctx.store.exists(rel) and not ctx.args.refresh:
            continue
        items = requests.get(f"{base}/search", headers=HEADERS, timeout=60,
                             params={"q": f"{stem}_2015_Da", "type": "file", "subtree": "glw_4", "per_page": 10}).json()["data"]["items"]
        hit = next((i for i in items if i["name"].lower().startswith(f"5_{stem.lower()}_2015_da")), None)
        if not hit:
            got.append(f"{label}: not found")
            continue
        raw = get_bytes(f"{base}/access/datafile/{hit['file_id']}", ctx.args.max_transfer, timeout=600)
        with MemoryFile(raw) as mf, mf.open() as src:
            win = from_bounds(*ctx.pad_bbox(0.5), transform=src.transform).round_offsets().round_lengths()
            arr, tf = src.read(1, window=win), src.window_transform(win)
            prof = src.profile.copy()
            prof.update(height=arr.shape[0], width=arr.shape[1], transform=tf, compress="deflate", driver="GTiff")
            with MemoryFile() as out:
                with out.open(**prof) as dst:
                    dst.write(arr, 1)
                data = out.read()
        ctx.store.write(rel, data, dict(dataset=f"Gridded Livestock of the World v4: {label} (heads/km2), clipped", provider="FAO / Harvard Dataverse",
                                        url=f"{base}/access/datafile/{hit['file_id']}", licence="CC BY 4.0 (check dataset page)",
                                        period="2015", resolution="~10 km", notes="no camels in GLW"))
        got.append(label)
    if not got:
        raise Skip("already present")
    return ", ".join(got)


def fetch_knbs_census(ctx):
    rel = "static/knbs_2019_census_vol2_marsabit_pages.txt"
    ctx.skip_if_have(rel)
    url = "https://www.knbs.or.ke/download/2019-kenya-population-and-housing-census-volume-ii-distribution-of-population-by-administrative-units/"
    pdf = get_bytes(url, ctx.args.max_transfer)
    if not pdf.startswith(b"%PDF"):
        raise Skip("KNBS returned a web page, not the PDF; download Volume II manually from knbs.or.ke")
    sha = hashlib.sha256(pdf).hexdigest()
    try:
        import pdfplumber
    except ImportError:
        ctx.store.write("static/knbs_2019_census_vol2.pdf", pdf, dict(dataset="KNBS 2019 Census Vol II (PDF)", provider="KNBS", url=url,
                                                                      licence="KNBS publication", period="2019", notes="install pdfplumber to extract Marsabit pages"))
        return "PDF saved (pdfplumber not installed)"
    pages = []
    try:
        with pdfplumber.open(io.BytesIO(pdf)) as doc:
            for i, page in enumerate(doc.pages, 1):
                t = page.extract_text() or ""
                if "MARSABIT" in t.upper():
                    pages.append(f"=== page {i} ===\n{t}")
    except Exception as e:
        raise Skip(f"could not read the PDF ({type(e).__name__}); download Volume II manually")
    ctx.store.write(rel, "\n\n".join(pages), dict(dataset="KNBS 2019 Census Vol II, pages mentioning Marsabit (text)", provider="KNBS", url=url,
                                                   licence="KNBS publication", period="2019", notes=f"source PDF sha256 {sha}; parse ward rows from this text"))
    return f"{len(pages)} pages extracted"


# ----------------------------------------------------------------------------- registry and runner
REGISTRY = {  # name: (function, group, rough size)
    "climate_indices": (fetch_indices, "web", "<0.1 MB"),
    "nasa_power": (fetch_nasa_power, "web", "~1-3 MB"),
    "wfp_prices": (fetch_wfp_prices, "web", "~1-3 MB"),
    "icpac_water": (fetch_icpac_water, "web", "<2 MB"),
    "wpdx": (fetch_wpdx, "web", "<3 MB"),
    **{k: (run_ee_series(k), "ee", "~0.3-2 MB") for k in EE_SERIES},
    "worldpop": (ee_worldpop, "ee", "<0.1 MB"),
    "ward_statics": (ee_static, "ee", "<0.1 MB"),
    "knbs_census": (fetch_knbs_census, "web", "<1 MB kept (10 MB transfer)"),
    "fews_lhz": (fetch_fews_lhz, "web", "<5 MB kept (transfer may be larger)"),
    "glw4": (fetch_glw4, "web", "<1 MB kept (large transfer)"),
}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wards", help="ward GeoJSON (EPSG:4326)")
    ap.add_argument("--code-field")
    ap.add_argument("--out", default="data/raw")
    ap.add_argument("--budget-mb", type=float, default=90.0)
    ap.add_argument("--max-file-mb", type=float, default=40.0)
    ap.add_argument("--max-transfer-mb", type=float, default=150.0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", nargs="+", choices=list(REGISTRY))
    ap.add_argument("--skip", nargs="+", choices=list(REGISTRY), default=[])
    ap.add_argument("--refresh", action="store_true", help="re-fetch files that already exist")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)
    args.max_transfer = int(args.max_transfer_mb * MB)
    if args.list:
        for k, (_, g, sz) in REGISTRY.items():
            print(f"{k:18} {g:4} {sz}")
        return 0
    if not args.wards:
        ap.error("--wards is required")
    wards, field = load_wards(args.wards, args.code_field)
    store = Store(args.out, args.budget_mb, args.max_file_mb)
    ctx = Ctx(args, store, wards)
    print(f"{len(wards)} wards (code column '{field}'); budget {args.budget_mb:.0f} MB; already used {store.used() / MB:.1f} MB")
    results = []
    for name, (fn, group, _) in REGISTRY.items():
        if (args.only and name not in args.only) or name in args.skip:
            continue
        t0 = time.time()
        try:
            note, status = fn(ctx), "ok"
        except Skip as e:
            note, status = str(e), "skipped"
        except Exception as e:
            note, status = f"{type(e).__name__}: {str(e)[:160]}", "FAILED"
        results.append((name, status, note))
        print(f"[{status:7}] {name:18} {note}  ({time.time() - t0:.0f}s, {store.used() / MB:.1f} MB used)")
    (store.root / "_fetch_report.json").write_text(json.dumps(
        [dict(dataset=n, status=s, note=m, date=TODAY.isoformat()) for n, s, m in results], indent=1))
    print(f"\nTotal on disk: {store.used() / MB:.1f} MB of {args.budget_mb:.0f} MB. Sources and checksums: {store.sources}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
