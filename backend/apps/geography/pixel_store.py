"""Pure helpers for pixel layers: quantise, pack, classify, render, zonal.

Everything here except store_layer() and load_ward_geoms() is free of Django
models and database access, so it can be unit-tested offline.

Honest limitations (read before trusting numbers)
-------------------------------------------------
* Quantisation floors VCI to an integer 0..100. With integer class breaks
  (20, 35, 50) class membership is exact. If NDMA validation moves a break to
  a non-integer (say 37.5) pixels within 1 VCI unit of it can be misclassified.
  Re-ingest with a finer encoding if that happens.
* Ward assignment uses pixel CENTRES. A ward with no pixel centre gets
  n_total = 0 and NULL shares, never zero percent. Overlapping ward polygons:
  the later one wins.
* NDVI is stored as uint8 with a 0.005 step (value = byte * 0.005 - 0.2, so
  -0.2 to 1.07). That is ROUNDED, accurate to about +-0.0025, and values below
  -0.2 (open water) are clipped to -0.2. The ward NDVI in WardIndicator keeps
  full precision, so pixel means will not match it exactly. NDVI has no class
  shares: there are no accepted NDVI classes for this project, so none are
  invented.
* read_geotiff() refuses anything that is not north-up EPSG:4326. It does
  not reproject.
"""
import io
import json
import re
import zlib

import numpy as np
from django.conf import settings
from django.db import connection, transaction
from PIL import Image

NODATA = 255
ENCODING = "u8-floor-0-100-nd255"
CLASS_NAMES = ("Emergency", "Alarm", "Alert", "Normal")
# ILLUSTRATIVE until validated against NDMA Marsabit bulletins.
DEFAULT_BREAKS = (20, 35, 50)
DEFAULT_COLORS = ((127, 29, 29, 255), (220, 38, 38, 255),
                  (245, 158, 11, 255), (22, 163, 74, 255))
NDVI_ENCODING = "u8-ndvi-step0.005-off-0.2-nd255"
NDVI_STEP = 0.005
NDVI_OFFSET = -0.2
# ILLUSTRATIVE colour ramp (NDVI value, RGB). Display only, not a classification.
NDVI_RAMP = ((-0.2, (120, 80, 40)), (0.05, (190, 140, 80)), (0.15, (235, 215, 130)),
             (0.3, (170, 205, 90)), (0.5, (60, 160, 60)), (0.8, (0, 90, 40)))
SPECS = {
    "vci": {"encoding": ENCODING, "classed": True},
    "ndvi": {"encoding": NDVI_ENCODING, "classed": False},
}
NODATA_SENTINEL_BELOW = -9000.0   # EE download uses unmask(-9999)

class EmptyRaster(ValueError):
    """Raster has no valid pixels (for example a fully cloud-masked composite)."""


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def get_breaks():
    return tuple(getattr(settings, "TERRA_VCI_BREAKS", DEFAULT_BREAKS))


def get_colors():
    return tuple(tuple(c) for c in getattr(settings, "TERRA_VCI_COLORS", DEFAULT_COLORS))


# ---------------------------------------------------------------- encoding
def quantise(arr):
    """float array (NaN = missing) -> uint8, floor(clip(v, 0, 100)), 255 = nodata."""
    a = np.asarray(arr, dtype="float64")
    out = np.full(a.shape, NODATA, dtype=np.uint8)
    ok = np.isfinite(a)
    out[ok] = np.floor(np.clip(a[ok], 0, 100)).astype(np.uint8)
    return out


def pack(u8):
    return zlib.compress(np.ascontiguousarray(u8, dtype=np.uint8).tobytes(), 6)


def unpack(blob, width, height):
    raw = zlib.decompress(bytes(blob))  # Postgres returns a memoryview
    if len(raw) != width * height:
        raise ValueError(f"stored array has {len(raw)} bytes, expected {width * height}")
    return np.frombuffer(raw, dtype=np.uint8).reshape(height, width)


def spec(indicator):
    try:
        return SPECS[indicator]
    except KeyError:
        raise ValueError(f"unknown indicator {indicator!r}; known: {sorted(SPECS)}") from None


def get_ndvi_ramp():
    return tuple((v, tuple(c)) for v, c in getattr(settings, "TERRA_NDVI_RAMP", NDVI_RAMP))


def quantise_ndvi(arr):
    """float NDVI (NaN = missing) -> uint8 0..254 (step 0.005, offset -0.2), 255 = nodata."""
    a = np.asarray(arr, dtype="float64")
    out = np.full(a.shape, NODATA, dtype=np.uint8)
    ok = np.isfinite(a)
    out[ok] = np.clip(np.rint((a[ok] - NDVI_OFFSET) / NDVI_STEP), 0, 254).astype(np.uint8)
    return out


def decode_ndvi(u8):
    out = u8.astype("float32") * NDVI_STEP + NDVI_OFFSET
    out[u8 == NODATA] = np.nan
    return out


def quantise_for(indicator, arr):
    spec(indicator)
    return quantise_ndvi(arr) if indicator == "ndvi" else quantise(arr)


def decode_value(indicator, byte):
    """Stored byte -> real value (VCI int, NDVI float rounded to 4 places)."""
    spec(indicator)
    return round(byte * NDVI_STEP + NDVI_OFFSET, 4) if indicator == "ndvi" else int(byte)


def class_name(indicator, byte):
    if not spec(indicator)["classed"]:
        return None
    return CLASS_NAMES[int(classify(np.array([byte], dtype=np.uint8))[0])]


# ---------------------------------------------------------- classify / render
def classify(u8, breaks=None):
    """0..len(breaks) per pixel (0 = worst), 255 stays nodata."""
    breaks = tuple(breaks or get_breaks())
    cls = np.digitize(u8, breaks).astype(np.uint8)  # b[i-1] <= x < b[i]
    cls[u8 == NODATA] = NODATA
    return cls


def render_png(u8, breaks=None, colors=None):
    """Coloured RGBA PNG bytes. Nodata pixels are fully transparent."""
    breaks = tuple(breaks or get_breaks())
    colors = np.array(colors or get_colors(), dtype=np.uint8)
    if len(colors) != len(breaks) + 1:
        raise ValueError("need len(breaks) + 1 colours")
    h, w = u8.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    valid = u8 != NODATA
    rgba[valid] = colors[classify(u8, breaks)[valid]]
    buf = io.BytesIO()
    Image.fromarray(rgba).save(buf, "PNG", optimize=True)
    return buf.getvalue()


def render_ndvi_png(u8, ramp=None):
    """Continuous colour ramp RGBA PNG. Nodata pixels are fully transparent."""
    ramp = ramp or get_ndvi_ramp()
    xs = [v for v, _ in ramp]
    lut = np.zeros((256, 4), dtype=np.uint8)
    vals = np.arange(255) * NDVI_STEP + NDVI_OFFSET
    for ch in range(3):
        lut[:255, ch] = np.rint(np.interp(vals, xs, [c[ch] for _, c in ramp])).astype(np.uint8)
    lut[:255, 3] = 255                      # byte 255 stays (0, 0, 0, 0)
    buf = io.BytesIO()
    Image.fromarray(lut[u8]).save(buf, "PNG", optimize=True)
    return buf.getvalue()


def render_for(indicator, u8):
    spec(indicator)
    return render_ndvi_png(u8) if indicator == "ndvi" else render_png(u8)


def sample(u8, bounds, lon, lat):
    """Value of the pixel containing (lon, lat), or None (outside or nodata)."""
    west, south, east, north = bounds
    h, w = u8.shape
    if not (west <= lon < east and south < lat <= north):
        return None
    col = min(int((lon - west) / (east - west) * w), w - 1)
    row = min(int((north - lat) / (north - south) * h), h - 1)
    v = int(u8[row, col])
    return None if v == NODATA else v


# ------------------------------------------------------------------ geotiff
def _check_north_up_4326(src):
    if src.crs is None or src.crs.to_epsg() != 4326:
        raise ValueError(f"expected EPSG:4326, got {src.crs}. Reproject first (not done here).")
    t = src.transform
    if abs(t.b) > 1e-12 or abs(t.d) > 1e-12 or t.e >= 0 or t.a <= 0:
        raise ValueError("raster is not north-up")


def read_geotiff(path, band=1, nodata_below=NODATA_SENTINEL_BELOW):
    """-> (float32 array with NaN for missing, (west, south, east, north))."""
    import rasterio
    with rasterio.open(path) as src:
        _check_north_up_4326(src)
        arr = src.read(band, masked=True).astype("float32").filled(np.nan)
        bounds = tuple(float(x) for x in src.bounds)
    arr[arr <= nodata_below] = np.nan
    return arr, bounds


def zonal_mean_all_touched(path, ward_geoms, band=1, nodata_below=NODATA_SENTINEL_BELOW):
    """Mean of every grid cell a ward touches -> {code: (mean | None, n_cells)}.

    Meant for COARSE grids (seasonal forecasts, 0.25 degrees or more). It is an
    unweighted mean of touched cells, not a downscaling and not area weighted.
    A ward smaller than one cell simply takes that cell's value.
    """
    import rasterio
    from rasterio.features import geometry_mask
    with rasterio.open(path) as src:
        _check_north_up_4326(src)
        data = src.read(band, masked=True).astype("float64").filled(np.nan)
        transform = src.transform
    data[data <= nodata_below] = np.nan
    out = {}
    for code, geom in ward_geoms.items():
        m = geometry_mask([geom], out_shape=data.shape, transform=transform,
                          invert=True, all_touched=True)
        vals = data[m]
        n_cells = int(vals.size)
        vals = vals[np.isfinite(vals)]
        out[code] = (float(vals.mean()) if vals.size else None, n_cells)
    return out


# ------------------------------------------------------------ ward class shares
def ward_class_shares(u8, bounds, ward_geoms, breaks=None):
    """{code: dict(n_total, n_valid, coverage, pct=[per class] | None)}."""
    from rasterio.features import rasterize
    from rasterio.transform import from_bounds
    breaks = tuple(breaks or get_breaks())
    h, w = u8.shape
    transform = from_bounds(*bounds, w, h)
    codes = list(ward_geoms)
    labels = rasterize([(ward_geoms[c], i + 1) for i, c in enumerate(codes)],
                       out_shape=(h, w), transform=transform, fill=0,
                       dtype="int32", all_touched=False)
    cls = classify(u8, breaks)
    n_cls = len(breaks) + 1
    res = {}
    for i, code in enumerate(codes, start=1):
        m = labels == i
        n_total = int(m.sum())
        c = cls[m]
        c = c[c != NODATA]
        n_valid = int(c.size)
        if n_total == 0:
            res[code] = dict(n_total=0, n_valid=0, coverage=None, pct=None)
            continue
        counts = np.bincount(c, minlength=n_cls)
        res[code] = dict(
            n_total=n_total, n_valid=n_valid,
            coverage=100.0 * n_valid / n_total,
            pct=(list(100.0 * counts / n_valid) if n_valid else None))
    return res


# ------------------------------------------------------------------ database
def load_ward_geoms(code_col, table="public.wards"):
    """{ward_code: GeoJSON geometry dict in EPSG:4326} via raw SQL (PostGIS).

    The column names of the imported boundary tables have never been seen by
    the author, so code_col is explicit. Run update_pixels --inspect to list
    the real columns. Geometry column is assumed to be `geom` (per handover).
    """
    if not _IDENT.match(code_col):
        raise ValueError("bad column name")
    if not all(_IDENT.match(p) for p in table.split(".")):
        raise ValueError("bad table name")
    sql = (f'SELECT "{code_col}", ST_AsGeoJSON(CASE WHEN ST_SRID(geom) = 0 '
           f'THEN ST_SetSRID(geom, 4326) ELSE ST_Transform(geom, 4326) END) FROM {table}')
    with connection.cursor() as cur:
        cur.execute(sql)
        return {str(code): json.loads(gj) for code, gj in cur.fetchall() if gj}


@transaction.atomic
def store_layer(*, indicator, period_start, period_end, bounds, source,
                methodology_version, arr_float=None, arr_u8=None,
                params=None, ward_geoms=None, status="Derived"):
    """Insert or REPLACE the layer for (indicator, period_start); recompute shares.

    Refuses an all-nodata raster so an empty period can never become "latest".
    """
    from .models import PixelLayer, WardClassShare
    sp = spec(indicator)
    u8 = quantise_for(indicator, arr_float) if arr_u8 is None else arr_u8
    if not (u8 != NODATA).any():
        raise EmptyRaster("raster has no valid pixels; refusing to store")
    h, w = u8.shape
    west, south, east, north = bounds
    layer, _ = PixelLayer.objects.update_or_create(
        indicator=indicator, period_start=period_start,
        defaults=dict(period_end=period_end, width=w, height=h, west=west,
                      south=south, east=east, north=north, values=pack(u8),
                      encoding=sp["encoding"], source=source,
                      methodology_version=methodology_version,
                      status=status, params=params or {}))
    layer.shares.all().delete()
    shares = {}
    if ward_geoms and sp["classed"]:
        breaks = get_breaks()
        shares = ward_class_shares(u8, bounds, ward_geoms, breaks)
        rows = []
        for code, s in shares.items():
            p = s["pct"] or [None] * 4
            rows.append(WardClassShare(
                layer=layer, ward_code=code, n_total=s["n_total"],
                n_valid=s["n_valid"], coverage=s["coverage"],
                pct_emergency=p[0], pct_alarm=p[1], pct_alert=p[2], pct_normal=p[3],
                breaks=",".join(str(b) for b in breaks)))
        WardClassShare.objects.bulk_create(rows)
    return layer, shares
