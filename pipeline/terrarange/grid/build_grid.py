from __future__ import annotations

from pathlib import Path

import geopandas as gpd


def build_grid(aoi_path: str | Path, cell_size_km: int = 5) -> gpd.GeoDataFrame:
    """Create a 5 km grid for the study area."""
    aoi = gpd.read_file(aoi_path)
    if aoi.crs is None:
        raise ValueError("AOI file has no CRS defined.")

    aoi = aoi.to_crs("EPSG:32637")
    cell_size_m = cell_size_km * 1000
    grid = gpd.GeoDataFrame(geometry=aoi.geometry.iloc[0].covering_grid(cell_size_m))
    grid["cell_id"] = range(len(grid))
    return grid


if __name__ == "__main__":
    grid = build_grid("config/pilot.yaml")
    print(f"Built {len(grid)} grid cells.")
