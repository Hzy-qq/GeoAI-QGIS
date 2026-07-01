from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import PermanentError


GEOMETRY_GROUPS = {
    "point": {"Point", "MultiPoint"},
    "line": {"LineString", "MultiLineString"},
    "polygon": {"Polygon", "MultiPolygon"},
    "any": {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"},
}


def validate_dataset(params: dict[str, Any]) -> dict[str, Any]:
    import geopandas as gpd

    path = Path(params["INPUT"])
    if not path.exists():
        raise PermanentError(f"Dataset does not exist: {path}")
    gdf = gpd.read_file(path)
    min_features = int(params.get("MIN_FEATURES", 1))
    max_features = int(params.get("MAX_FEATURES", 200_000))
    expected = str(params.get("GEOMETRY_TYPE", "any")).lower()
    if expected not in GEOMETRY_GROUPS:
        raise PermanentError(f"Unsupported GEOMETRY_TYPE: {expected}")
    if len(gdf) < min_features:
        raise PermanentError(f"Dataset has {len(gdf)} features; expected at least {min_features}.")
    if len(gdf) > max_features:
        raise PermanentError(f"Dataset has {len(gdf)} features; limit is {max_features}.")
    if gdf.crs is None:
        raise PermanentError("Dataset has no CRS.")
    geometry_types = set(gdf.geometry.geom_type.dropna())
    unexpected = geometry_types - GEOMETRY_GROUPS[expected]
    if unexpected:
        raise PermanentError(
            f"Unexpected geometry types {sorted(unexpected)}; expected {expected}."
        )
    null_count = int(gdf.geometry.isna().sum())
    empty_count = int(gdf.geometry.is_empty.sum())
    invalid_count = int((~gdf.geometry.is_valid).sum())
    if null_count or empty_count or invalid_count:
        raise PermanentError(
            "Dataset geometry validation failed: "
            f"null={null_count}, empty={empty_count}, invalid={invalid_count}."
        )
    return {
        "input": str(path),
        "feature_count": int(len(gdf)),
        "crs": str(gdf.crs),
        "geometry_types": sorted(geometry_types),
        "valid": True,
    }
