from __future__ import annotations

from typing import Iterable

import geopandas as gpd


def apply_restrictions(grid: gpd.GeoDataFrame, exclusion_layers: Iterable[gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    """Remove cells overlapping restricted or non-rangeland areas."""
    filtered = grid.copy()
    for layer in exclusion_layers:
        filtered = filtered.loc[~filtered.intersects(layer.unary_union)]
    return filtered
