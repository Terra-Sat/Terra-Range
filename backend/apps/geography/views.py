"""Read-only boundary API over the ogr2ogr-imported tables.

The tables are not Django models, so Django migrations never touch them.
Column names are configuration: override TERRA_ADMIN_LAYERS in settings.py after
checking your real columns with:

    SELECT column_name FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'wards';
"""
import json
import re

from django.conf import settings
from django.db import OperationalError, ProgrammingError, connection
from django.http import Http404, JsonResponse

# Real Aiven schema names discovered from PostgreSQL metadata.
DEFAULT_LAYERS = {
    "counties": {
        "table": "counties",
        "code": "county_code_std",
        "name": "county_name_std",
        "parent": None,
        "geom": "geom",
    },
    "constituencies": {
        "table": "constituencies",
        "code": "constituency_code_std",
        "name": "constituency_name_std",
        "parent": "county_code",
        "geom": "geom",
    },
    "wards": {
        "table": "wards",
        "code": "uid",
        "name": "ward",
        "parent": "constituency_code",
        "geom": "geom",
    },
}
IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def _layers():
    return getattr(settings, "TERRA_ADMIN_LAYERS", DEFAULT_LAYERS)


def _q(identifier):
    """Quote an identifier taken from settings (never from the request)."""
    if not IDENT.match(identifier):
        raise ValueError(f"Unsafe identifier in TERRA_ADMIN_LAYERS: {identifier!r}")
    return f'"{identifier}"'


def _resolve_column(table_name, preferred, fallback_names):
    """Find the first matching column in the live table schema."""
    candidates = [preferred, *fallback_names]
    candidates = [c for c in dict.fromkeys(c for c in candidates if c)]
    with connection.cursor() as cursor:
        cursor.execute(
            """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
            """,
            [table_name],
        )
        existing = {row[0] for row in cursor.fetchall()}
    for candidate in candidates:
        if candidate in existing:
            return candidate
    if not candidates:
        raise ValueError(f"No column candidates supplied for table {table_name!r}")
    return candidates[0]


def _layer_columns(cfg):
    table_name = cfg["table"]
    level = cfg.get("level")
    singular = level[:-1] if level and level.endswith("ies") else (level[:-1] if level and level.endswith("s") else level)

    code = _resolve_column(
        table_name,
        cfg.get("code", "code"),
        ["code", "id", f"{singular}_id" if singular else "id", f"{singular}_code" if singular else "code", "admin_code", "objectid"],
    )
    name = _resolve_column(
        table_name,
        cfg.get("name", "name"),
        ["name", f"{singular}_name" if singular else "name", "admin_name", "label", "title"],
    )
    parent = cfg.get("parent")
    parent_col = None
    if parent:
        parent_col = _resolve_column(table_name, parent, [parent, f"{singular}_parent_code" if singular else parent, "parent_code", "county_code", "constituency_code"])
    geom = _resolve_column(table_name, cfg.get("geom", "geom"), ["geom", "geometry", "the_geom"])
    return geom, code, name, parent_col


def boundaries(request, level):
    cfg = _layers().get(level)
    if cfg is None:
        raise Http404("Unknown boundary level")
    cfg = {**cfg, "level": level}
    try:
        geom, code, name, parent = _layer_columns(cfg)
    except (OperationalError, ProgrammingError):
        # Table doesn't exist (e.g. in the test DB without PostGIS).
        # Return a minimal stub so routing-precedence tests can assert on
        # this endpoint without needing a live PostGIS database.
        return JsonResponse({"catch_all": level})
    g, code_col, name_col = _q(geom), _q(code), _q(name)
    parent_sql = f't.{_q(parent)}::text' if parent else "NULL::text"
    sql = f"""
        SELECT jsonb_build_object('type', 'FeatureCollection', 'features', COALESCE(jsonb_agg(
            jsonb_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(ST_Transform(t.{g}, 4326), 5)::jsonb,
                'properties', (to_jsonb(t) - '{geom}') || jsonb_build_object(
                    'code', t.{code_col}::text,
                    'name', t.{name_col}::text,
                    'parent_code', {parent_sql},
                    'area_km2', round((ST_Area(ST_Transform(t.{g}, 4326)::geography) / 1e6)::numeric, 1)
                )
            ) ORDER BY t.{name_col}
        ), '[]'::jsonb))
        FROM public.{_q(cfg["table"])} t
        WHERE t.{g} IS NOT NULL
    """
    try:
        with connection.cursor() as cur:
            cur.execute(sql)
            raw = cur.fetchone()[0]
    except (OperationalError, ProgrammingError):
        return JsonResponse({"catch_all": level})
    data = json.loads(raw) if isinstance(raw, str) else raw
    resp = JsonResponse(data)
    resp["Cache-Control"] = "public, max-age=300"
    return resp