"""GET /api/v1/indicators/?indicator=vci3&level=wards|constituencies|counties[&start=YYYY-MM-DD&end=...][&code=...]

Default: latest period only. Coarser levels are area-weighted means of ward values; `coverage` is the share of
area (not of wards) that had a valid value, so gaps are visible rather than averaged away.
"""
from collections import defaultdict

from django.db import connection
from django.http import JsonResponse

from .models import WardIndicator
from .views import _layers, _q


def _ward_meta():
    c = _layers()["wards"]
    g, code = _q(c["geom"]), _q(c["code"])
    parent = f"{_q(c['parent'])}::text" if c.get("parent") else "NULL::text"
    sql = (f"SELECT {code}::text, {parent}, ST_Area(ST_Transform({g}, 4326)::geography) / 1e6 "
           f"FROM public.{_q(c['table'])} WHERE {g} IS NOT NULL")
    with connection.cursor() as cur:
        cur.execute(sql)
        return {r[0]: (r[1], float(r[2])) for r in cur.fetchall()}


def indicators(request):
    ind = request.GET.get("indicator", "vci3")
    level = request.GET.get("level", "wards")
    qs = WardIndicator.objects.filter(indicator=ind)
    if request.GET.get("start"):
        qs = qs.filter(period_start__gte=request.GET["start"])
    if request.GET.get("end"):
        qs = qs.filter(period_start__lte=request.GET["end"])
    if not request.GET.get("start") and not request.GET.get("end"):
        latest = qs.order_by("-period_start").values_list("period_start", flat=True).first()
        qs = qs.filter(period_start=latest) if latest else qs.none()
    rows = list(qs.values("ward_code", "period_start", "value", "valid_fraction", "source", "methodology_version"))
    meta = _ward_meta()

    if level == "wards":
        out = [dict(code=r["ward_code"], period_start=r["period_start"], value=r["value"],
                    valid_fraction=r["valid_fraction"], status="Derived" if r["source"] == "derived" else "Observed")
               for r in rows if not request.GET.get("code") or r["ward_code"] == request.GET["code"]]
    else:
        groups = defaultdict(lambda: defaultdict(list))
        for r in rows:
            parent, area = meta.get(r["ward_code"], (None, 0.0))
            key = parent if level == "constituencies" else "county"
            groups[key][r["period_start"]].append((r["value"], area))
        total = defaultdict(float)
        for code, (parent, area) in meta.items():
            total[parent if level == "constituencies" else "county"] += area
        out = []
        for key, periods in groups.items():
            for d, vals in periods.items():
                ok = [(v, a) for v, a in vals if v is not None]
                wsum = sum(a for _, a in ok)
                out.append(dict(code=key, period_start=d, status="Derived",
                                value=sum(v * a for v, a in ok) / wsum if wsum else None,
                                coverage=round(wsum / total[key], 3) if total[key] else None))
        if request.GET.get("code"):
            out = [o for o in out if o["code"] == request.GET["code"]]
    return JsonResponse({"indicator": ind, "level": level, "rows": sorted(out, key=lambda o: (o["code"] or "", o["period_start"]))})
