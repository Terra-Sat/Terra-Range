"""GET /water/points/   GeoJSON for the whole region (filters: kind, ward, source, status)
   GET /water/summary/?level=wards|constituencies|counties   counts per unit, to tailor info for WAOs"""
from collections import defaultdict

from django.http import JsonResponse

from .indicator_views import _ward_meta
from .models import WaterPoint


def _status(p):
    verified = str(p.get("verified_status") or "").lower()
    if verified.startswith("verified"):
        return "verified"
    reported = str(p.get("reported_status") or "unknown").lower()
    return "reported" if reported != "unknown" else "unverified"


def _public():
    return WaterPoint.objects.filter(access_restriction="public", ward_code__isnull=False)


def points(request):
    qs = _public()
    for key, field in (("kind", "kind"), ("ward", "ward_code"), ("source", "source")):
        if request.GET.get(key):
            qs = qs.filter(**{field: request.GET[key]})
    feats = []
    for p in qs.values("id", "name", "kind", "source", "lat", "lon", "ward_code", "reported_status", "verified_status", "verified_at"):
        st = _status(p)
        if request.GET.get("status") and st != request.GET["status"]:
            continue
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                      "properties": {"id": p["id"], "name": p["name"], "kind": p["kind"], "source": p["source"], "ward": p["ward_code"],
                                     "status": st, "reported": p["reported_status"], "verified_at": p["verified_at"]}})
    return JsonResponse({"type": "FeatureCollection", "features": feats})


def summary(request):
    level, meta = request.GET.get("level", "wards"), _ward_meta()
    acc = defaultdict(lambda: {"total": 0, "verified": 0, "by_kind": defaultdict(int), "last_verified": None})
    area = defaultdict(float)
    for code, (parent, a) in meta.items():
        area[code if level == "wards" else (parent if level == "constituencies" else "county")] += a
    for p in _public().values("ward_code", "kind", "reported_status", "verified_status", "verified_at"):
        parent = meta.get(p["ward_code"], (None, 0))[0]
        key = p["ward_code"] if level == "wards" else (parent if level == "constituencies" else "county")
        r = acc[key]
        r["total"] += 1
        r["verified"] += _status(p) == "verified"
        r["by_kind"][p["kind"]] += 1
        if p["verified_at"] and (r["last_verified"] is None or p["verified_at"] > r["last_verified"]):
            r["last_verified"] = p["verified_at"]
    rows = [dict(code=k, total=r["total"], verified=r["verified"], by_kind=dict(r["by_kind"]), last_verified=r["last_verified"],
                 per_1000_km2=round(r["total"] / area[k] * 1000, 2) if area[k] else None) for k, r in acc.items()]
    for k in area:                                                      # units with none mapped are still listed
        if k not in acc:
            rows.append(dict(code=k, total=0, verified=0, by_kind={}, last_verified=None, per_1000_km2=0.0))
    return JsonResponse({"level": level, "rows": sorted(rows, key=lambda r: str(r["code"]))})
