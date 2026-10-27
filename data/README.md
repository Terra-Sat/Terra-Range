# Data handling

This repository keeps raw, immutable inputs separate from generated products.

## Rules

- Store raw data in `data/raw/` with one folder per source and acquisition date.
- Never edit or overwrite raw files in place.
- Record checksums and provenance in the associated source manifest.
- Processed outputs belong in `data/processed/` with a `manifest.json` containing run metadata, methodology version, source IDs, and row counts.
- `data/demo/` is for example data and smoke tests only.
