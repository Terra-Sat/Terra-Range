from __future__ import annotations

import geopandas as gpd


def ensure_analysis_crs(gdf: gpd.GeoDataFrame, crs_code: str = "EPSG:32637") -> gpd.GeoDataFrame:
    """Ensure a GeoDataFrame is in the configured analysis CRS."""
    if gdf.crs is None:
        raise ValueError("GeoDataFrame has no CRS assigned.")
    if gdf.crs.to_string() != crs_code:
        return gdf.to_crs(crs_code)
    return gdf
