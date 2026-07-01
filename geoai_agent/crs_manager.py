from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import PermanentError


def choose_local_projected_crs(gdf) -> str:
    if gdf.empty or gdf.crs is None:
        raise PermanentError("Cannot choose CRS for empty or unreferenced data.")
    geographic = gdf.to_crs("EPSG:4326")
    centroid = geographic.geometry.union_all().centroid
    longitude, latitude = float(centroid.x), float(centroid.y)
    zone = max(1, min(60, int((longitude + 180) // 6) + 1))
    epsg = (32600 if latitude >= 0 else 32700) + zone
    return f"EPSG:{epsg}"


def _write(gdf, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    gdf.to_file(output, driver="GPKG")


def auto_reproject_layer(params: dict[str, Any]) -> dict[str, Any]:
    import geopandas as gpd

    source = gpd.read_file(params["INPUT"])
    target_crs = choose_local_projected_crs(source)
    projected = source.to_crs(target_crs)
    output = Path(params["OUTPUT"])
    _write(projected, output)
    return {
        "output": str(output),
        "feature_count": int(len(projected)),
        "source_crs": str(source.crs),
        "target_crs": target_crs,
    }


def reproject_to_match(params: dict[str, Any]) -> dict[str, Any]:
    import geopandas as gpd

    source = gpd.read_file(params["INPUT"])
    reference = gpd.read_file(params["REFERENCE"])
    if source.crs is None or reference.crs is None:
        raise PermanentError("Both source and reference layers must have a CRS.")
    projected = source.to_crs(reference.crs)
    output = Path(params["OUTPUT"])
    _write(projected, output)
    return {
        "output": str(output),
        "feature_count": int(len(projected)),
        "source_crs": str(source.crs),
        "target_crs": str(reference.crs),
    }
