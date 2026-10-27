# Terra-Range

Terra-Range is a geospatial decision-support platform for rangeland monitoring, climate risk, water access, and field advisory workflows across the study area.

## Architecture

This repository follows the split between a reproducible pipeline and a web application:

- `config/`: versioned science configuration and thresholds
- `pipeline/`: Python package for raster processing, indicator generation, and export
- `backend/`: Django application for storing and serving processed run results
- `frontend/`: static UI shell and translated pages
- `briefings/`: monthly assessment pack generation
- `docs/`: methodology and operational documentation

## Build order

1. `config/`, `pipeline/core`, `pipeline/grid`
2. `acquire/gee`, `validate`, `indicators/rangeland`
3. `validation_vs_ndma`
4. `backend/geography` and `observations`, then `load_run`
5. Everything else following the Track B order

## Commands

```bash
make grid
make vci
make load
make briefing
```

## Operational rules

- Raw data is immutable and stored outside the git tree or under `data/raw/` with a checksum manifest.
- Each processed result must carry `methodology_version` and `source_id` metadata.
- Grid cell IDs are the canonical join key across all tabular products.
- Raster products should remain COG-based and served through TiTiler; only tabular results are stored in Postgres.

## Local stack

```bash
docker compose up --build
```

The base stack includes PostGIS, the Django backend, TiTiler, and pg_tileserv.
