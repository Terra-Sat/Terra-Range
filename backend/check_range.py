"""Read-only check of the 20-year NDVI range used by the VCI mask.

Uses the same settings as update_pixels defaults: Terra MOD13Q1, baseline
2001-2020, SummaryQA <= 1. Reports, over pixels that are valid in the stored
composite, how many a given --min-range would keep.
"""
import os
from datetime import timedelta

import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

import ee
from .models import PixelLayer

COL, BASE, QA_MAX = "MODIS/061/MOD13Q1", (2001, 2020), 1
THRESH = [0.0, 0.05, 0.10, 0.15, 0.20]

layer = PixelLayer.objects.filter(indicator="vci").order_by("-period_start").first()
if layer is None:
    raise SystemExit("No stored vci layer. Run the single-composite trial first.")
W, S, E, N = layer.bounds
d0 = layer.period_start
doy = d0.timetuple().tm_yday
print(f"composite {d0} (day of year {doy}), bounds {W:.3f},{S:.3f},{E:.3f},{N:.3f}")

ee.Initialize(project=os.environ["EE_PROJECT"])
region = ee.Geometry.Rectangle([W, S, E, N], "EPSG:4326", False)


def ndvi(i):
    return i.select("NDVI").multiply(0.0001).updateMask(i.select("SummaryQA").lte(QA_MAX))


base = (ee.ImageCollection(COL)
        .filter(ee.Filter.calendarRange(doy, doy, "day_of_year"))
        .filter(ee.Filter.calendarRange(BASE[0], BASE[1], "year"))
        .map(ndvi))
cur_img = ee.ImageCollection(COL).filterDate(d0.isoformat(), (d0 + timedelta(days=1)).isoformat()).first()
cur_valid = ndvi(ee.Image(cur_img)).mask()
rng = base.max().subtract(base.min()).rename("range")
n_years = base.count().rename("n_years")
KW = dict(geometry=region, scale=250, maxPixels=int(1e9), tileScale=4)

try:
    print(f"baseline composites for this date: {base.size().getInfo()}")

    pct = ee.Reducer.percentile([5, 10, 25, 50, 75, 90])
    stats = (ee.Image.cat([rng, n_years]).updateMask(cur_valid)
             .reduceRegion(reducer=pct, **KW).getInfo())

    kept_img = ee.Image.cat(
        [rng.gte(t).unmask(0).rename(f"keep_{t:.2f}") for t in THRESH]
        + [cur_valid.rename("cur_valid")])
    keep = kept_img.updateMask(cur_valid).reduceRegion(reducer=ee.Reducer.mean(), **KW).getInfo()
    cur = kept_img.select("cur_valid").reduceRegion(reducer=ee.Reducer.mean(), **KW).getInfo()
except Exception as e:                       # quota, timeout or auth problems land here
    raise SystemExit(f"Earth Engine call failed: {type(e).__name__}: {e}")

print("\nvalid share of the stored composite (compare ~90% NDVI in your trial): "
      f"{100 * cur['cur_valid']:.1f}%")
print("\n20-year NDVI range at valid pixels (percentiles):")
for p in (5, 10, 25, 50, 75, 90):
    print(f"  p{p:<2}  range {stats.get(f'range_p{p}', float('nan')):.3f}   "
          f"valid baseline years {stats.get(f'n_years_p{p}', float('nan')):.0f}")
print("\nShare of valid pixels that VCI would KEEP at each --min-range:")
for t in THRESH:
    tag = "  (= baseline defined at all)" if t == 0.0 else ""
    print(f"  min-range {t:.2f}: {100 * keep[f'keep_{t:.2f}']:.1f}%{tag}")
print("\nYour trial stored VCI valid / NDVI valid = 43.2 / 90.2 = 47.9%, so if the mask "
      "explains the gap, 'min-range 0.10' above should be close to 48%.")
