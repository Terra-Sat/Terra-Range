"""Shared indicator logic: storing rows and deriving VCI / anomaly / SPI. No raster or Earth Engine dependencies,
so both update_indicators (Planetary Computer) and update_gee (Earth Engine) can use it."""
import numpy as np
import pandas as pd
from scipy import stats

from .models import WardIndicator

METH = "1.0"
MIN_VALID = 0.5                 # publish a ward NDVI only if >= 50% of its pixels are valid
VCI_BASE = (2001, 2025)         # baseline years for VCI / anomaly
RAIN_BASE = (1991, 2020)        # baseline years for percent-of-normal and SPI


def save(rows):
    objs = [WardIndicator(methodology_version=METH, **r) for r in rows]
    WardIndicator.objects.bulk_create(
        objs, batch_size=2000, update_conflicts=True,
        unique_fields=["ward_code", "indicator", "period_start"],
        update_fields=["period_end", "value", "valid_fraction", "source", "methodology_version"])


def clean(v):
    return None if v is None or pd.isna(v) else float(v)


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
        nd["vci"] = np.where(rng > 0.01, np.clip(100 * (nd["value"] - nd["min"]) / rng, 0, 100), np.nan)   # outside-baseline values clip to 0 / 100
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
