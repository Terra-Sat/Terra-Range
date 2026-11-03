"""GET /api/v1/indicators/?indicator=vci3&level=wards|constituencies|counties[&start=YYYY-MM-DD&end=...][&code=...]

Default: latest period only. Coarser levels are area-weighted means of ward values; `coverage` is the share of
area (not of wards) that had a valid value, so gaps are visible rather than averaged away.
"""
from collections import defaultdict

from django.db import OperationalError, ProgrammingError, connection
from django.http import JsonResponse

from .models import WardIndicator
from .views import _layers, _q


def _ward_hierarchy():
    """Return ward metadata including constituency and county ancestry.

    Keeping the legacy ward->(parent_code, area_km2) mapping for callers that
    only need the immediate parent, while providing county ancestry for summary
    rollups across multiple counties.
    """
    wards = _layers()["wards"]
    consts = _layers().get("constituencies")
    ward_geom, ward_code = _q(wards["geom"]), _q(wards["code"])
    ward_parent = f"{_q(wards['parent'])}::text" if wards.get("parent") else "NULL::text"
    if not consts:
        try:
            with connection.cursor() as cur:
                cur.execute(
                    f"SELECT {ward_code}::text, {ward_parent}, NULL::text, "
                    f"ST_Area(ST_Transform({ward_geom}, 4326)::geography) / 1e6 "
                    f"FROM public.{_q(wards['table'])} WHERE {ward_geom} IS NOT NULL"
                )
                return {r[0]: {"constituency": r[1], "county": r[2], "area_km2": float(r[3])} for r in cur.fetchall()}
        except (OperationalError, ProgrammingError):
            return {}

    const_table = _q(consts["table"])
    const_code = _q(consts["code"])
    const_parent = f"{_q(consts['parent'])}::text" if consts.get("parent") else "NULL::text"
    sql = (
        f"SELECT w.{ward_code}::text AS ward_code, "
        f"w.{_q(wards['parent'])}::text AS constituency_code, "
        f"c.{const_parent} AS county_code, "
        f"ST_Area(ST_Transform(w.{ward_geom}, 4326)::geography) / 1e6 "
        f"FROM public.{_q(wards['table'])} w "
        f"LEFT JOIN public.{const_table} c ON c.{const_code}::text = w.{_q(wards['parent'])}::text "
        f"WHERE w.{ward_geom} IS NOT NULL"
    )
    try:
        with connection.cursor() as cur:
            cur.execute(sql)
            return {
                r[0]: {
                    "constituency": r[1],
                    "county": r[2],
                    "area_km2": float(r[3]),
                }
                for r in cur.fetchall()
            }
    except (OperationalError, ProgrammingError):
        return {}


def _ward_meta():
    """Return {ward_code: (parent_code, area_km2)} from the PostGIS boundary table.
    Returns an empty dict in the test environment (SQLite, no PostGIS tables).
    """
    hierarchy = _ward_hierarchy()
    return {code: (info["constituency"], info["area_km2"]) for code, info in hierarchy.items()}


def _county_for_ward(ward_code):
    hierarchy = _ward_hierarchy()
    return hierarchy.get(ward_code, {}).get("county")


def indicators(request):
    ind = request.GET.get("indicator", "vci3")
    level = request.GET.get("level", "wards")
    qs = WardIndicator.objects.filter(indicator=ind)
    if request.GET.get("start"):
        qs = qs.filter(period_start__gte=request.GET["start"])
    if request.GET.get("end"):
        qs = qs.filter(period_start__lte=request.GET["end"])
    if not request.GET.get("start") and not request.GET.get("end"):
        latest = (qs.filter(value__isnull=False).order_by("-period_start").values_list("period_start", flat=True).first()
                  or qs.order_by("-period_start").values_list("period_start", flat=True).first())
        qs = qs.filter(period_start=latest) if latest else qs.none()
    rows = list(qs.values("ward_code", "period_start", "value", "valid_fraction", "source", "methodology_version"))
    meta = _ward_meta()

    if level == "wards":
        out = [dict(code=r["ward_code"], period_start=r["period_start"], value=r["value"],
                    valid_fraction=r["valid_fraction"], status="Derived" if r["source"] == "derived" else "Observed")
               for r in rows if not request.GET.get("code") or r["ward_code"] == request.GET["code"]]
    else:
        groups = defaultdict(lambda: defaultdict(list))
        totals = defaultdict(float)
        hierarchy = _ward_hierarchy()

        for code, info in hierarchy.items():
            area = info.get("area_km2", meta.get(code, (None, 0.0))[1])
            parent = info.get("constituency") or meta.get(code, (None, 0.0))[0]
            county = info.get("county") or parent
            key = parent if level == "constituencies" else county
            totals[key] += area

        for r in rows:
            info = hierarchy.get(r["ward_code"], {})
            area = info.get("area_km2", meta.get(r["ward_code"], (None, 0.0))[1])
            parent = info.get("constituency") or meta.get(r["ward_code"], (None, 0.0))[0]
            county = info.get("county") or parent
            key = parent if level == "constituencies" else county
            groups[key][r["period_start"]].append((r["value"], area))

        out = []
        for key, periods in groups.items():
            for d, vals in periods.items():
                ok = [(v, a) for v, a in vals if v is not None]
                wsum = sum(a for _, a in ok)
                out.append(dict(code=key, period_start=d, status="Derived",
                                value=sum(v * a for v, a in ok) / wsum if wsum else None,
                                coverage=round(wsum / totals.get(key, 1.0), 3) if totals.get(key) else None))
        if request.GET.get("code"):
            out = [o for o in out if o["code"] == request.GET["code"]]
    return JsonResponse({"indicator": ind, "level": level, "rows": sorted(out, key=lambda o: (o["code"] or "", o["period_start"]))})