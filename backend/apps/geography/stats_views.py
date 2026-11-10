"""GET /stats/?level=wards|constituencies|counties   surface-water areas and population (additive, so rollups are sums)
   GET /data-quality/                                  what is stored, how complete it is, and the latest pipeline runs"""
import re
from collections import defaultdict

from django.db.models import Max, Min
from django.http import JsonResponse

from .indicator_views import _ward_hierarchy, _ward_meta
from .models import IngestRun, WardIndicator, WardStat, WaterPoint


def stats(request):
    level, meta = request.GET.get("level", "wards"), _ward_meta()
    hierarchy = _ward_hierarchy()
    acc = defaultdict(lambda: defaultdict(float))
    for r in WardStat.objects.exclude(value__isnull=True).values("ward_code", "stat", "value"):
        info = hierarchy.get(r["ward_code"], {})
        parent = info.get("constituency") or meta.get(r["ward_code"], (None, 0))[0]
        county = info.get("county") or parent
        key = r["ward_code"] if level == "wards" else (parent if level == "constituencies" else county)
        acc[key][r["stat"]] += r["value"]
    rows = []
    for key, s in acc.items():
        row = {"code": key, **{n: round(v, 2) for n, v in s.items()}}
        area = s.get("ward_area_km2")
        if area:
            for n in ("permanent", "seasonal", "max_extent"):
                row[f"water_{n}_pct"] = round(100 * s.get(f"water_{n}_km2", 0) / area, 2)
        pop_key = next((n for n in s if re.fullmatch(r"pop_\d{4}", n)), None)
        if pop_key and s[pop_key]:
            for n in [n for n in s if n.startswith("pop_within_")]:
                row[n.replace("_water", "") + "_pct"] = round(100 * s[n] / s[pop_key], 1)
        rows.append(row)
    return JsonResponse({"level": level, "status": "Estimated",
                         "notes": "Population is a WorldPop estimate; distances to water are straight-line to any mapped point "
                                  "(verified or not). Surface water is JRC history, not current conditions.",
                         "rows": sorted(rows, key=lambda r: str(r["code"]))})


def data_quality(request):
    inds = []
    for ind in sorted(WardIndicator.objects.values_list("indicator", flat=True).distinct()):
        q = WardIndicator.objects.filter(indicator=ind)
        a, ok = q.aggregate(lo=Min("period_start"), hi=Max("period_start")), q.filter(value__isnull=False)
        inds.append({"indicator": ind, "rows": q.count(), "non_null": ok.count(), "first": a["lo"], "last": a["hi"],
                     "last_with_data": ok.aggregate(m=Max("period_start"))["m"]})
    wp = WaterPoint.objects.filter(ward_code__isnull=False)
    runs = list(IngestRun.objects.order_by("-started_at").values("command", "status", "started_at", "finished_at", "rows_written", "notes")[:10])
    return JsonResponse({"indicators": inds,
                         "water_points": {"in_wards": wp.count(), "with_seasonality": wp.filter(season__isnull=False).count(),
                                          "verified": wp.filter(verified_status__startswith="verified").count()},
                         "runs": runs})


def climate_drivers(request):
    """GET /api/v1/boundaries/climate/
    Returns parsed summary of ENSO (ONI), IOD (DMI), and WFP food & livestock prices.
    """
    from .models import ClimateIndex
    import csv, io
    data = {"oni": None, "dmi": None, "prices": []}

    # 1. Parse ONI
    try:
        oni_obj = ClimateIndex.objects.filter(name="oni_cpc").first()
        if oni_obj:
            lines = oni_obj.get_text().strip().splitlines()
            records = []
            for line in lines[1:]:
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        records.append({
                            "season": parts[0],
                            "year": int(parts[1]),
                            "anom": float(parts[3]),
                        })
                    except (ValueError, IndexError):
                        continue
            if records:
                latest = records[-1]
                anom = latest["anom"]
                phase = "El Niño" if anom >= 0.5 else ("La Niña" if anom <= -0.5 else "Neutral")
                data["oni"] = {
                    "latest_season": f"{latest['season']} {latest['year']}",
                    "latest_anom": anom,
                    "phase": phase,
                    "recent": records[-12:],
                }
    except Exception:
        pass

    # 2. Parse DMI (Indian Ocean Dipole)
    try:
        dmi_obj = ClimateIndex.objects.filter(name="dmi_hadisst_psl").first()
        if dmi_obj:
            lines = dmi_obj.get_text().strip().splitlines()
            records = []
            months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            for line in lines[1:]:
                parts = line.split()
                if len(parts) == 13:
                    try:
                        yr = int(parts[0])
                        for m_idx, v_str in enumerate(parts[1:]):
                            v = float(v_str)
                            if -90 < v < 90:
                                records.append({"year": yr, "month": months[m_idx], "val": round(v, 2)})
                    except (ValueError, IndexError):
                        continue
            if records:
                latest = records[-1]
                val = latest["val"]
                phase = "Positive IOD" if val >= 0.4 else ("Negative IOD" if val <= -0.4 else "Neutral")
                data["dmi"] = {
                    "latest_date": f"{latest['month']} {latest['year']}",
                    "latest_val": val,
                    "phase": phase,
                    "recent": records[-12:],
                }
    except Exception:
        pass

    # 3. Parse WFP Food & Livestock Prices
    try:
        wfp_obj = ClimateIndex.objects.filter(name="wfp_food_prices_kenya_north").first()
        if wfp_obj:
            text = wfp_obj.get_text()
            reader = csv.DictReader(io.StringIO(text))
            commodities = defaultdict(list)
            for r in reader:
                comm = r.get("commodity")
                price = r.get("price")
                if comm and price:
                    try:
                        p_val = float(price)
                        commodities[comm].append({
                            "date": r.get("date"),
                            "market": r.get("market"),
                            "county": r.get("admin2"),
                            "price": p_val,
                            "unit": r.get("unit"),
                        })
                    except ValueError:
                        continue
            price_summary = []
            for comm, items in commodities.items():
                if not items:
                    continue
                items.sort(key=lambda x: x["date"])
                latest_items = [it for it in items if it["date"] == items[-1]["date"]]
                avg_price = sum(it["price"] for it in latest_items) / len(latest_items)
                price_summary.append({
                    "commodity": comm,
                    "unit": items[-1]["unit"],
                    "latest_date": items[-1]["date"],
                    "avg_price": round(avg_price, 1),
                    "min_price": min(it["price"] for it in latest_items),
                    "max_price": max(it["price"] for it in latest_items),
                    "markets_count": len(latest_items),
                })
            prio = lambda c: 0 if "maize" in c.lower() else (1 if "goat" in c.lower() else (2 if "cattle" in c.lower() else (3 if "camel" in c.lower() else 4)))
            price_summary.sort(key=lambda x: (prio(x["commodity"]), x["commodity"]))
            data["prices"] = price_summary[:12]
    except Exception:
        pass

    return JsonResponse(data)
