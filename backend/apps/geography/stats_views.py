"""GET /stats/?level=wards|constituencies|counties   surface-water areas and population (additive, so rollups are sums)
   GET /data-quality/                                  what is stored, how complete it is, and the latest pipeline runs"""
import re
from collections import defaultdict

from django.db.models import Max, Min
from django.http import JsonResponse

from .indicator_views import _ward_meta
from .models import IngestRun, WardIndicator, WardStat, WaterPoint


def stats(request):
    level, meta = request.GET.get("level", "wards"), _ward_meta()
    acc = defaultdict(lambda: defaultdict(float))
    for r in WardStat.objects.exclude(value__isnull=True).values("ward_code", "stat", "value"):
        parent = meta.get(r["ward_code"], (None, 0))[0]
        key = r["ward_code"] if level == "wards" else (parent if level == "constituencies" else "county")
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
