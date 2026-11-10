"""API for stored pixel layers and seasonal outlooks.

Routes (see pixel_urls.py). The catch-all <str:level>/ route in urls.py MUST
stay after these.
"""
import hashlib
from datetime import date

from django.http import HttpResponse, HttpResponseNotModified, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from . import pixel_store as ps
from .models import PixelLayer, SeasonalOutlook, WardClassShare

OUTLOOK_NOTE = ("External seasonal outlook, Reported by the issuing centre. "
                "Not a Terra Range prediction and not validated for Marsabit.")


def _legend(breaks):
    edges = [None] + list(breaks) + [None]
    out = []
    for i, name in enumerate(ps.CLASS_NAMES[: len(breaks) + 1]):
        lo, hi = edges[i], edges[i + 1]
        out.append({"class": name,
                    "range": (f"<{hi}" if lo is None else f">={lo}" if hi is None else f"{lo} to <{hi}")})
    return out


def _ramp_legend():
    return [{"value": val, "color": "#%02x%02x%02x" % tuple(c)} for val, c in ps.get_ndvi_ramp()]


def _meta(layer, base_path):
    w, s, e, n = layer.bounds
    return {
        "id": layer.pk, "indicator": layer.indicator,
        "period_start": layer.period_start.isoformat(),
        "period_end": layer.period_end.isoformat(),
        "bounds": [w, s, e, n],
        "coordinates": [[w, n], [e, n], [e, s], [w, s]],  # MapLibre image source order
        "width": layer.width, "height": layer.height,
        "image_url": f"{base_path}{layer.pk}/image.png",
        "status": layer.status, "methodology_version": layer.methodology_version,
        "source": layer.source, "params": layer.params, "encoding": layer.encoding,
    }


@require_GET
def pixel_layers(request):
    """?indicator=vci&period=latest|YYYY-MM-DD  -> layer list + selected id."""
    indicator = request.GET.get("indicator", "vci")
    period = request.GET.get("period", "latest")
    layers = list(PixelLayer.objects.filter(indicator=indicator)
                  .defer("values").order_by("-period_start")[:60])
    selected = None
    if period == "latest":
        selected = layers[0] if layers else None
    else:
        try:
            want = date.fromisoformat(period)
        except ValueError:
            return JsonResponse({"error": "period must be 'latest' or YYYY-MM-DD"}, status=400)
        selected = next((l for l in layers if l.period_start == want), None)
    try:
        classed = ps.spec(indicator)["classed"]
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    breaks = ps.get_breaks() if classed else ()
    base = request.path if request.path.endswith("/") else request.path + "/"
    return JsonResponse({
        "indicator": indicator,
        "selected": selected.pk if selected else None,
        "breaks": list(breaks),
        "legend_type": "classes" if classed else "ramp",
        "legend": _legend(breaks) if classed else _ramp_legend(),
        "layers": [_meta(l, base) for l in layers],
        "note": ("Classes are ILLUSTRATIVE until validated against NDMA bulletins. "
                 "Pixel values are Derived and will not equal ward averages." if classed else
                 "Colour ramp is ILLUSTRATIVE, not a classification. NDVI is stored rounded "
                 "to 0.005 and clipped at -0.2, so it will not equal ward NDVI exactly."),
    })


@require_GET
def pixel_image(request, pk):
    layer = get_object_or_404(PixelLayer, pk=pk)
    breaks = ps.get_breaks() if ps.spec(layer.indicator)["classed"] else ps.get_ndvi_ramp()
    etag = f'W/"{layer.pk}-{int(layer.updated_at.timestamp())}-{hashlib.md5(str(breaks).encode()).hexdigest()[:8]}"'
    if request.headers.get("If-None-Match") == etag:
        return HttpResponseNotModified()
    u8 = ps.unpack(layer.values, layer.width, layer.height)
    resp = HttpResponse(ps.render_for(layer.indicator, u8), content_type="image/png")
    resp["ETag"] = etag
    resp["Cache-Control"] = "public, max-age=3600"
    return resp


@require_GET
def pixel_value(request, pk):
    """?lat=..&lon=..  -> value of the pixel under the point (for click popups)."""
    try:
        lat = float(request.GET["lat"])
        lon = float(request.GET["lon"])
    except (KeyError, ValueError):
        return JsonResponse({"error": "lat and lon are required numbers"}, status=400)
    layer = get_object_or_404(PixelLayer, pk=pk)
    u8 = ps.unpack(layer.values, layer.width, layer.height)
    b = ps.sample(u8, layer.bounds, lon, lat)
    value = None if b is None else ps.decode_value(layer.indicator, b)
    cls = None if b is None else ps.class_name(layer.indicator, b)
    note = ("Integer floor of the pixel value (about 250 m)." if layer.indicator == "vci"
            else "Rounded to 0.005, so accurate to about 0.0025 (about 250 m pixel).")
    return JsonResponse({"indicator": layer.indicator, "value": value, "class": cls,
                         "status": layer.status, "note": note})


@require_GET
def pixel_shares(request, pk):
    layer = get_object_or_404(PixelLayer.objects.defer("values"), pk=pk)
    if not ps.spec(layer.indicator)["classed"]:
        return JsonResponse({"layer": layer.pk, "wards": [], "classes": [],
                             "note": f"No class shares for {layer.indicator}: "
                                     "there are no accepted classes for it."})
    rows = WardClassShare.objects.filter(layer=layer).order_by("ward_code")
    return JsonResponse({
        "layer": layer.pk, "period_start": layer.period_start.isoformat(),
        "classes": list(ps.CLASS_NAMES), "status": "Derived",
        "wards": [{"ward_code": r.ward_code, "coverage": r.coverage,
                   "n_valid": r.n_valid, "n_total": r.n_total, "breaks": r.breaks,
                   "pct": [r.pct_emergency, r.pct_alarm, r.pct_alert, r.pct_normal]}
                  for r in rows]})


@require_GET
def outlook(request):
    """?issued=latest|YYYY-MM-DD[&variable=..][&source=..][&ward=..]

    One block per source, each with leads 1..n. Missing values stay null.
    """
    issued = request.GET.get("issued", "latest")
    qs = SeasonalOutlook.objects.all()
    for key, field in (("variable", "variable"), ("source", "source"), ("ward", "ward_code")):
        if request.GET.get(key):
            qs = qs.filter(**{field: request.GET[key]})
    want = None
    if issued != "latest":
        try:
            want = date.fromisoformat(issued)
        except ValueError:
            return JsonResponse({"error": "issued must be 'latest' or YYYY-MM-DD"}, status=400)
    blocks = []
    for src in qs.order_by().values_list("source", flat=True).distinct():
        sq = qs.filter(source=src)
        when = want or sq.order_by("-issued").values_list("issued", flat=True).first()
        rows = list(sq.filter(issued=when).order_by("lead", "variable", "ward_code"))
        if not rows:
            continue
        leads = {}
        for r in rows:
            blk = leads.setdefault(str(r.lead), {
                "valid_start": r.valid_start.isoformat(),
                "valid_end": r.valid_end.isoformat(), "variables": {}})
            v = blk["variables"].setdefault(r.variable, {"units": r.units, "wards": {}})
            v["wards"][r.ward_code] = r.value
        blocks.append({"source": src, "issued": when.isoformat(), "status": "Reported",
                       "methodology_version": rows[0].methodology_version, "leads": leads})
    return JsonResponse({"outlooks": blocks, "note": OUTLOOK_NOTE,
                         "lead_convention": "Lead 1 is the calendar month of issue."})
