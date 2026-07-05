from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .crs_manager import auto_reproject_layer, reproject_to_match
from .data_acquisition import DATA_ACQUISITION_HANDLERS
from .data_validation import validate_dataset
from .errors import classify_error


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def prepare_output(value: str) -> Path:
    path = resolve_path(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    return path


def read_layer(value: str):
    import geopandas as gpd

    path = resolve_path(value)
    if not path.exists():
        raise FileNotFoundError(f"Input layer does not exist: {path}")
    return gpd.read_file(path)


def write_layer(gdf, output: str) -> Path:
    path = prepare_output(output)
    gdf.to_file(path, driver="GPKG")
    return path


def normalize_name(value: Any) -> str:
    return "".join(str(value).strip().lower().split())


def load_neighbor_boundaries(params: dict[str, Any]) -> dict[str, Any]:
    source = PROJECT_ROOT / "data" / "fixtures" / "nanjing_neighbor_cities.gpkg"
    if not source.exists():
        raise FileNotFoundError(f"Bundled neighbor-boundary fixture is missing: {source}")
    gdf = read_layer(str(source))
    if "region_name" not in gdf.columns:
        raise ValueError("Neighbor-boundary fixture has no region_name field.")
    expected = normalize_name(params["REGION_NAME"])
    if not gdf["region_name"].astype(str).map(normalize_name).eq(expected).any():
        raise ValueError(
            f"The bundled adjacency fixture does not cover {params['REGION_NAME']}. "
            "Currently it is intended for the Nanjing acceptance scenario."
        )
    output_path = write_layer(gdf, params["OUTPUT"])
    return {
        "feature_count": int(len(gdf)),
        "data_source": "bundled_gadm_4_1_fixture",
        "output": str(output_path),
    }


def select_feature_by_attribute(params: dict[str, Any]) -> dict[str, Any]:
    gdf = read_layer(params["INPUT"])
    field = params["FIELD"]
    if field not in gdf.columns:
        raise ValueError(f"Field '{field}' not found. Available fields: {list(gdf.columns)}")

    expected = normalize_name(params["VALUE"])
    normalized = gdf[field].fillna("").map(normalize_name)
    selected = gdf[normalized == expected].copy()
    if selected.empty:
        selected = gdf[normalized.str.contains(expected, regex=False)].copy()
    if selected.empty:
        raise ValueError(f"No feature matched {field}={params['VALUE']!r}")

    output_path = write_layer(selected, params["OUTPUT"])
    return {
        "selected_count": int(len(selected)),
        "selected_values": selected[field].astype(str).tolist(),
        "output": str(output_path),
    }


def reproject_layer(params: dict[str, Any]) -> dict[str, Any]:
    gdf = read_layer(params["INPUT"])
    if gdf.crs is None:
        raise ValueError("Input layer has no CRS and cannot be reprojected.")
    projected = gdf.to_crs(params["TARGET_CRS"])
    output_path = write_layer(projected, params["OUTPUT"])
    return {
        "feature_count": int(len(projected)),
        "target_crs": str(projected.crs),
        "output": str(output_path),
    }


def calculate_polygon_area(params: dict[str, Any]) -> dict[str, Any]:
    gdf = read_layer(params["INPUT"])
    if gdf.crs is None:
        raise ValueError("Input layer has no CRS.")
    if getattr(gdf.crs, "is_geographic", False):
        raise ValueError("Area calculation requires a projected CRS with meter units.")
    if gdf.empty:
        raise ValueError("Input polygon layer is empty.")

    area_field = params["AREA_FIELD"]
    result = gdf.copy()
    result[area_field] = result.geometry.area / 1_000_000
    output_path = write_layer(result, params["OUTPUT"])
    return {
        "area_field": area_field,
        "area_sq_km": round(float(result[area_field].sum()), 4),
        "feature_count": int(len(result)),
        "output": str(output_path),
    }


def find_adjacent_polygons(params: dict[str, Any]) -> dict[str, Any]:
    polygons = read_layer(params["INPUT"])
    target = read_layer(params["TARGET"])
    name_field = params["NAME_FIELD"]
    if name_field not in polygons.columns or name_field not in target.columns:
        raise ValueError(f"NAME_FIELD '{name_field}' must exist in both layers.")
    if polygons.crs is None or target.crs is None:
        raise ValueError("Both polygon layers must have a CRS.")
    if target.crs != polygons.crs:
        target = target.to_crs(polygons.crs)

    metric_crs = "EPSG:3857"
    metric_polygons = polygons.to_crs(metric_crs)
    metric_target = target.to_crs(metric_crs)
    target_geometry = metric_target.geometry.union_all()
    tolerance_m = float(params.get("TOLERANCE_M", 0))

    mask = metric_polygons.geometry.touches(target_geometry)
    if tolerance_m > 0:
        mask = mask | (metric_polygons.geometry.distance(target_geometry) <= tolerance_m)

    target_names = {normalize_name(value) for value in target[name_field].astype(str)}
    not_target = ~polygons[name_field].astype(str).map(normalize_name).isin(target_names)
    adjacent = polygons[mask & not_target].copy()
    adjacent = adjacent.sort_values(name_field)
    output_path = write_layer(adjacent, params["OUTPUT"])
    return {
        "adjacent_count": int(len(adjacent)),
        "adjacent_names": adjacent[name_field].astype(str).tolist(),
        "tolerance_m": tolerance_m,
        "output": str(output_path),
    }


def count_points_in_polygon(params: dict[str, Any]) -> dict[str, Any]:
    polygons = read_layer(params["POLYGONS"])
    points = read_layer(params["POINTS"])
    if polygons.crs is None or points.crs is None:
        raise ValueError("Polygon and point layers must have a CRS.")
    if points.crs != polygons.crs:
        points = points.to_crs(polygons.crs)
    if polygons.empty:
        raise ValueError("Polygon layer is empty.")

    polygon_geometry = polygons.geometry.union_all()
    inside = points[points.geometry.intersects(polygon_geometry)].copy()
    count_field = params["COUNT_FIELD"]
    result = polygons.copy()
    result[count_field] = int(len(inside))
    if "data_source" in points.columns:
        result["point_data_source"] = ", ".join(
            sorted(set(points["data_source"].dropna().astype(str)))
        )
    if "data_version" in points.columns:
        result["point_data_version"] = ", ".join(
            sorted(set(points["data_version"].dropna().astype(str)))
        )
    output_path = write_layer(result, params["OUTPUT"])
    return {
        "point_count": int(len(inside)),
        "count_field": count_field,
        "point_names": inside.get("name", []).astype(str).tolist()
        if "name" in inside.columns else [],
        "output": str(output_path),
    }


PYTHON_TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "load_neighbor_boundaries": load_neighbor_boundaries,
    "select_feature_by_attribute": select_feature_by_attribute,
    "reproject_layer": reproject_layer,
    "calculate_polygon_area": calculate_polygon_area,
    "find_adjacent_polygons": find_adjacent_polygons,
    "count_points_in_polygon": count_points_in_polygon,
    "validate_dataset": validate_dataset,
    "auto_reproject_layer": auto_reproject_layer,
    "reproject_to_match": reproject_to_match,
    **DATA_ACQUISITION_HANDLERS,
}


def run_python_tool(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    handler = PYTHON_TOOL_HANDLERS.get(tool_name)
    if handler is None:
        raise ValueError(f"No Python GIS handler registered for tool: {tool_name}")
    try:
        metrics = handler(params)
    except Exception as exc:
        return {
            "algorithm": f"python:{tool_name}",
            "params": params,
            "command": f"python handler {tool_name}",
            "returncode": 1,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "success": False,
            "metrics": {},
            "error_type": classify_error(exc),
        }
    return {
        "algorithm": f"python:{tool_name}",
        "params": params,
        "command": f"python handler {tool_name}",
        "returncode": 0,
        "stdout": json.dumps(metrics, ensure_ascii=False),
        "stderr": "",
        "success": True,
        "metrics": metrics,
    }
