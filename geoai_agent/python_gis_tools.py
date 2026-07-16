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


def multi_criteria_site_selection(params: dict[str, Any]) -> dict[str, Any]:
    import geopandas as gpd
    from shapely.geometry import box

    boundary = read_layer(params["BOUNDARY"])
    facilities = read_layer(params["FACILITIES"])
    roads = read_layer(params["ROADS"])
    if boundary.empty or facilities.empty or roads.empty:
        raise ValueError("Boundary, facilities and roads must all contain features.")
    if boundary.crs is None or getattr(boundary.crs, "is_geographic", False):
        raise ValueError("Site selection requires a projected boundary CRS with metre units.")
    if facilities.crs != boundary.crs:
        facilities = facilities.to_crs(boundary.crs)
    if roads.crs != boundary.crs:
        roads = roads.to_crs(boundary.crs)

    cell_size = float(params.get("CELL_SIZE", 4000))
    top_n = int(params.get("TOP_N", 10))
    max_road_distance = float(params.get("MAX_ROAD_DISTANCE", 3000))
    max_facility_distance = float(params.get("MAX_FACILITY_DISTANCE", 5000))
    weights = {
        "road": float(params.get("ROAD_WEIGHT", 0.45)),
        "facility": float(params.get("FACILITY_WEIGHT", 0.35)),
        "interior": float(params.get("INTERIOR_WEIGHT", 0.20)),
    }
    total_weight = sum(weights.values())
    if total_weight <= 0:
        raise ValueError("At least one site-selection weight must be positive.")
    weights = {name: value / total_weight for name, value in weights.items()}

    region = boundary.geometry.union_all()
    min_x, min_y, max_x, max_y = region.bounds
    rows: list[dict[str, Any]] = []
    x = min_x
    candidate_id = 0
    while x < max_x:
        y = min_y
        while y < max_y:
            grid_cell = box(x, y, min(x + cell_size, max_x), min(y + cell_size, max_y))
            clipped = grid_cell.intersection(region)
            if not clipped.is_empty and clipped.area >= grid_cell.area * 0.25:
                candidate_id += 1
                point = clipped.representative_point()
                road_distance = float(roads.geometry.distance(point).min())
                facility_distance = float(facilities.geometry.distance(point).min())
                boundary_clearance = float(point.distance(region.boundary))
                road_score = max(0.0, 1.0 - road_distance / max_road_distance)
                facility_score = max(
                    0.0, 1.0 - facility_distance / max_facility_distance
                )
                interior_score = min(1.0, boundary_clearance / max(cell_size, 1.0))
                total_score = 100 * (
                    road_score * weights["road"]
                    + facility_score * weights["facility"]
                    + interior_score * weights["interior"]
                )
                rows.append(
                    {
                        "candidate_id": candidate_id,
                        "site_score": round(total_score, 2),
                        "road_score": round(road_score * 100, 2),
                        "facility_score": round(facility_score * 100, 2),
                        "interior_score": round(interior_score * 100, 2),
                        "road_distance_m": round(road_distance, 2),
                        "facility_distance_m": round(facility_distance, 2),
                        "boundary_clearance_m": round(boundary_clearance, 2),
                        "area_sq_km": round(clipped.area / 1_000_000, 4),
                        "geometry": clipped,
                    }
                )
            y += cell_size
        x += cell_size
    if not rows:
        raise ValueError("No candidate cells could be generated inside the boundary.")

    result = gpd.GeoDataFrame(rows, geometry="geometry", crs=boundary.crs)
    result = result.sort_values(
        ["site_score", "road_distance_m", "facility_distance_m"],
        ascending=[False, True, True],
    ).head(top_n).copy()
    result["rank"] = range(1, len(result) + 1)
    result = result.to_crs("EPSG:4326")
    output_path = write_layer(result, params["OUTPUT"])
    return {
        "candidate_count": int(len(result)),
        "best_score": float(result["site_score"].max()),
        "weights": weights,
        "cell_size_m": cell_size,
        "output": str(output_path),
    }


def _grid_cells(boundary, cell_size: float):
    import geopandas as gpd
    from shapely.geometry import box

    region = boundary.geometry.union_all()
    min_x, min_y, max_x, max_y = region.bounds
    rows = []
    grid_id = 0
    x = min_x
    while x < max_x:
        y = min_y
        while y < max_y:
            cell = box(x, y, min(x + cell_size, max_x), min(y + cell_size, max_y))
            clipped = cell.intersection(region)
            if not clipped.is_empty and clipped.area >= cell.area * 0.1:
                grid_id += 1
                rows.append({"grid_id": grid_id, "geometry": clipped})
            y += cell_size
        x += cell_size
    if not rows:
        raise ValueError("No analysis grid cells could be generated inside the boundary.")
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=boundary.crs)


def point_density_grid(params: dict[str, Any]) -> dict[str, Any]:
    boundary = read_layer(params["BOUNDARY"])
    points = read_layer(params["POINTS"])
    if boundary.empty or boundary.crs is None or getattr(boundary.crs, "is_geographic", False):
        raise ValueError("Point-density analysis requires a projected non-empty boundary.")
    if points.crs != boundary.crs:
        points = points.to_crs(boundary.crs)
    cell_size = float(params.get("CELL_SIZE", 5000))
    grid = _grid_cells(boundary, cell_size)
    counts = [0] * len(grid)
    grid_geometries = list(grid.geometry)
    for point in points.geometry:
        if point is None or point.is_empty:
            continue
        # A point on a shared grid edge must belong to one cell only.
        cell_index = next(
            (index for index, geometry in enumerate(grid_geometries) if geometry.covers(point)),
            None,
        )
        if cell_index is not None:
            counts[cell_index] += 1
    grid["point_count"] = counts
    grid["area_sq_km"] = grid.geometry.area / 1_000_000
    grid["density_per_sq_km"] = grid["point_count"] / grid["area_sq_km"].clip(lower=1e-9)
    grid = grid.sort_values(["density_per_sq_km", "point_count"], ascending=False)
    output_path = write_layer(grid.to_crs("EPSG:4326"), params["OUTPUT"])
    return {
        "grid_count": int(len(grid)),
        "point_count": int(len(points)),
        "max_density": round(float(grid["density_per_sq_km"].max()), 4),
        "cell_size_m": cell_size,
        "output": str(output_path),
    }


def line_density_grid(params: dict[str, Any]) -> dict[str, Any]:
    boundary = read_layer(params["BOUNDARY"])
    lines = read_layer(params["LINES"])
    if boundary.empty or boundary.crs is None or getattr(boundary.crs, "is_geographic", False):
        raise ValueError("Line-density analysis requires a projected non-empty boundary.")
    if lines.crs != boundary.crs:
        lines = lines.to_crs(boundary.crs)
    cell_size = float(params.get("CELL_SIZE", 5000))
    grid = _grid_cells(boundary, cell_size)
    lengths = []
    for geometry in grid.geometry:
        candidates = lines[lines.geometry.intersects(geometry)]
        length_m = float(candidates.geometry.intersection(geometry).length.sum())
        lengths.append(length_m)
    grid["road_length_km"] = [value / 1000 for value in lengths]
    grid["area_sq_km"] = grid.geometry.area / 1_000_000
    grid["density_km_per_sq_km"] = (
        grid["road_length_km"] / grid["area_sq_km"].clip(lower=1e-9)
    )
    grid = grid.sort_values("density_km_per_sq_km", ascending=False)
    output_path = write_layer(grid.to_crs("EPSG:4326"), params["OUTPUT"])
    return {
        "grid_count": int(len(grid)),
        "road_length_km": round(float(grid["road_length_km"].sum()), 3),
        "max_density": round(float(grid["density_km_per_sq_km"].max()), 4),
        "cell_size_m": cell_size,
        "output": str(output_path),
    }


def nearest_distance_to_features(params: dict[str, Any]) -> dict[str, Any]:
    source = read_layer(params["INPUT"])
    target = read_layer(params["TARGET"])
    if source.empty or target.empty:
        raise ValueError("Nearest-distance analysis requires non-empty input and target layers.")
    if source.crs is None or getattr(source.crs, "is_geographic", False):
        raise ValueError("Nearest-distance analysis requires a projected input CRS.")
    if target.crs != source.crs:
        target = target.to_crs(source.crs)
    distance_field = params["DISTANCE_FIELD"]
    target_union = target.geometry.union_all()
    result = source.copy()
    result[distance_field] = result.geometry.distance(target_union)
    output_path = write_layer(result.to_crs("EPSG:4326"), params["OUTPUT"])
    return {
        "feature_count": int(len(result)),
        "minimum_distance_m": round(float(result[distance_field].min()), 2),
        "mean_distance_m": round(float(result[distance_field].mean()), 2),
        "maximum_distance_m": round(float(result[distance_field].max()), 2),
        "distance_field": distance_field,
        "output": str(output_path),
    }


def nearest_neighbor_analysis(params: dict[str, Any]) -> dict[str, Any]:
    """Measure each POI's Euclidean distance to the nearest other POI."""
    import numpy as np

    points = read_layer(params["INPUT"])
    if points.crs is None or getattr(points.crs, "is_geographic", False):
        raise ValueError("Nearest-neighbour analysis requires a projected CRS with metre units.")
    if len(points) < 2:
        raise ValueError("Nearest-neighbour analysis requires at least two point features.")
    if not points.geometry.geom_type.isin(["Point"]).all():
        raise ValueError("Nearest-neighbour analysis only accepts point geometry.")

    coordinates = np.column_stack((points.geometry.x.to_numpy(), points.geometry.y.to_numpy()))
    nearest_indexes: list[int] = []
    nearest_distances: list[float] = []
    for index, coordinate in enumerate(coordinates):
        squared = np.square(coordinates - coordinate).sum(axis=1)
        squared[index] = np.inf
        target = int(np.argmin(squared))
        nearest_indexes.append(target)
        nearest_distances.append(float(np.sqrt(squared[target])))

    distance_field = params.get("DISTANCE_FIELD", "nearest_neighbor_m")
    result = points.copy()
    result[distance_field] = nearest_distances
    result["nearest_index"] = nearest_indexes
    if "name" in points.columns:
        names = points["name"].fillna("").astype(str).tolist()
        result["nearest_name"] = [names[index] for index in nearest_indexes]
    output_path = write_layer(result, params["OUTPUT"])
    values = np.asarray(nearest_distances)
    return {
        "feature_count": int(len(result)),
        "distance_field": distance_field,
        "minimum_distance_m": round(float(values.min()), 2),
        "mean_distance_m": round(float(values.mean()), 2),
        "median_distance_m": round(float(np.median(values)), 2),
        "maximum_distance_m": round(float(values.max()), 2),
        "output": str(output_path),
    }


def service_gap_analysis(params: dict[str, Any]) -> dict[str, Any]:
    """Subtract dissolved service coverage from a projected administrative boundary."""
    import geopandas as gpd

    boundary = read_layer(params["BOUNDARY"])
    coverage = read_layer(params["COVERAGE"])
    if boundary.empty:
        raise ValueError("Boundary layer is empty.")
    if boundary.crs is None or getattr(boundary.crs, "is_geographic", False):
        raise ValueError("Service-gap analysis requires a projected boundary CRS.")
    if coverage.crs != boundary.crs:
        coverage = coverage.to_crs(boundary.crs)

    boundary_geometry = boundary.geometry.union_all()
    coverage_geometry = coverage.geometry.union_all() if not coverage.empty else None
    clipped_coverage = (
        boundary_geometry.intersection(coverage_geometry)
        if coverage_geometry is not None else None
    )
    gap_geometry = (
        boundary_geometry.difference(clipped_coverage)
        if clipped_coverage is not None else boundary_geometry
    )
    boundary_area = float(boundary_geometry.area)
    gap_area = float(gap_geometry.area)
    coverage_area = max(0.0, boundary_area - gap_area)
    coverage_rate = 100.0 * coverage_area / boundary_area if boundary_area else 0.0

    result = gpd.GeoDataFrame(
        [{
            "uncovered_sq_km": gap_area / 1_000_000,
            "covered_sq_km": coverage_area / 1_000_000,
            "coverage_rate_pct": coverage_rate,
            "distance_m": float(params["DISTANCE"]),
        }],
        geometry=[gap_geometry],
        crs=boundary.crs,
    )
    output_path = write_layer(result, params["OUTPUT"])
    return {
        "uncovered_sq_km": round(gap_area / 1_000_000, 4),
        "covered_sq_km": round(coverage_area / 1_000_000, 4),
        "coverage_rate_pct": round(coverage_rate, 2),
        "distance_m": float(params["DISTANCE"]),
        "output": str(output_path),
    }


def multi_ring_service_analysis(params: dict[str, Any]) -> dict[str, Any]:
    """Create cumulative service-area rings and quantify marginal coverage gains."""
    import geopandas as gpd

    boundary = read_layer(params["BOUNDARY"])
    points = read_layer(params["POINTS"])
    if boundary.empty or points.empty:
        raise ValueError("Boundary and POI layers must contain features.")
    if boundary.crs is None or getattr(boundary.crs, "is_geographic", False):
        raise ValueError("Multi-ring analysis requires a projected boundary CRS.")
    if points.crs != boundary.crs:
        points = points.to_crs(boundary.crs)
    distances = sorted({
        float(value.strip())
        for value in str(params.get("DISTANCES", "500,1000,2000")).split(",")
        if value.strip()
    })
    if not distances or any(value <= 0 for value in distances) or len(distances) > 6:
        raise ValueError("DISTANCES must contain one to six positive comma-separated metres.")

    boundary_geometry = boundary.geometry.union_all()
    boundary_area = float(boundary_geometry.area)
    rows: list[dict[str, Any]] = []
    geometries = []
    previous_area = 0.0
    for distance in distances:
        coverage_geometry = points.geometry.buffer(distance, resolution=12).union_all()
        coverage_geometry = boundary_geometry.intersection(coverage_geometry)
        area = float(coverage_geometry.area)
        rows.append({
            "distance_m": distance,
            "coverage_sq_km": area / 1_000_000,
            "coverage_rate_pct": 100.0 * area / boundary_area if boundary_area else 0.0,
            "marginal_gain_sq_km": max(0.0, area - previous_area) / 1_000_000,
        })
        geometries.append(coverage_geometry)
        previous_area = area
    result = gpd.GeoDataFrame(rows, geometry=geometries, crs=boundary.crs)
    output_path = write_layer(result, params["OUTPUT"])
    return {
        "ring_count": len(result),
        "distances_m": distances,
        "maximum_coverage_sq_km": round(float(result["coverage_sq_km"].max()), 4),
        "maximum_coverage_rate_pct": round(float(result["coverage_rate_pct"].max()), 2),
        "output": str(output_path),
    }


def advanced_site_selection(params: dict[str, Any]) -> dict[str, Any]:
    """Campus-style selection with hard road/transit/water constraints and ranked scores."""
    import geopandas as gpd

    boundary = read_layer(params["BOUNDARY"])
    facilities = read_layer(params["FACILITIES"])
    transit = read_layer(params["TRANSIT"])
    roads = read_layer(params["ROADS"])
    water = read_layer(params["WATER"])
    layers = (boundary, facilities, transit, roads, water)
    if any(layer.empty for layer in layers):
        raise ValueError("Advanced site selection requires non-empty input layers.")
    if boundary.crs is None or getattr(boundary.crs, "is_geographic", False):
        raise ValueError("Advanced site selection requires a projected boundary CRS.")
    facilities = facilities.to_crs(boundary.crs) if facilities.crs != boundary.crs else facilities
    transit = transit.to_crs(boundary.crs) if transit.crs != boundary.crs else transit
    roads = roads.to_crs(boundary.crs) if roads.crs != boundary.crs else roads
    water = water.to_crs(boundary.crs) if water.crs != boundary.crs else water

    cell_size = float(params.get("CELL_SIZE", 2000))
    top_n = int(params.get("TOP_N", 15))
    max_road = float(params.get("MAX_ROAD_DISTANCE", 1000))
    max_transit = float(params.get("MAX_TRANSIT_DISTANCE", 3000))
    max_facility = float(params.get("MAX_FACILITY_DISTANCE", 5000))
    min_water = float(params.get("MIN_WATER_DISTANCE", 500))
    weights = {
        "road": float(params.get("ROAD_WEIGHT", 0.35)),
        "transit": float(params.get("TRANSIT_WEIGHT", 0.30)),
        "facility": float(params.get("FACILITY_WEIGHT", 0.20)),
        "interior": float(params.get("INTERIOR_WEIGHT", 0.15)),
    }
    total_weight = sum(weights.values())
    if total_weight <= 0:
        raise ValueError("At least one advanced site-selection weight must be positive.")
    weights = {name: value / total_weight for name, value in weights.items()}

    grid = _grid_cells(boundary, cell_size)
    region = boundary.geometry.union_all()
    water_union = water.geometry.union_all()
    rows = []
    for item in grid.itertuples():
        geometry = item.geometry
        point = geometry.representative_point()
        water_overlap = float(geometry.intersection(water_union).area / max(geometry.area, 1.0))
        water_distance = float(point.distance(water_union))
        road_distance = float(roads.geometry.distance(point).min())
        transit_distance = float(transit.geometry.distance(point).min())
        facility_distance = float(facilities.geometry.distance(point).min())
        if water_overlap > 0.01 or water_distance < min_water:
            continue
        if road_distance > max_road or transit_distance > max_transit:
            continue
        boundary_clearance = float(point.distance(region.boundary))
        road_score = max(0.0, 1.0 - road_distance / max_road)
        transit_score = max(0.0, 1.0 - transit_distance / max_transit)
        facility_score = max(0.0, 1.0 - facility_distance / max_facility)
        interior_score = min(1.0, boundary_clearance / max(cell_size, 1.0))
        score = 100 * (
            road_score * weights["road"]
            + transit_score * weights["transit"]
            + facility_score * weights["facility"]
            + interior_score * weights["interior"]
        )
        rows.append(
            {
                "candidate_id": item.grid_id,
                "site_score": round(score, 2),
                "road_distance_m": round(road_distance, 2),
                "transit_distance_m": round(transit_distance, 2),
                "facility_distance_m": round(facility_distance, 2),
                "water_distance_m": round(water_distance, 2),
                "water_overlap_pct": round(water_overlap * 100, 4),
                "boundary_clearance_m": round(boundary_clearance, 2),
                "area_sq_km": round(geometry.area / 1_000_000, 4),
                "geometry": geometry,
            }
        )
    if not rows:
        raise ValueError(
            "No candidate cell satisfies the road, transit and water constraints. "
            "Increase distance thresholds or reduce MIN_WATER_DISTANCE."
        )
    result = gpd.GeoDataFrame(rows, geometry="geometry", crs=boundary.crs)
    result = result.sort_values(
        ["site_score", "road_distance_m", "transit_distance_m"],
        ascending=[False, True, True],
    ).head(top_n).copy()
    result["rank"] = range(1, len(result) + 1)
    output_path = write_layer(result.to_crs("EPSG:4326"), params["OUTPUT"])
    return {
        "candidate_count": int(len(result)),
        "best_score": float(result["site_score"].max()),
        "constraints": {
            "max_road_distance_m": max_road,
            "max_transit_distance_m": max_transit,
            "min_water_distance_m": min_water,
        },
        "weights": weights,
        "output": str(output_path),
    }


PYTHON_TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "load_neighbor_boundaries": load_neighbor_boundaries,
    "select_feature_by_attribute": select_feature_by_attribute,
    "reproject_layer": reproject_layer,
    "calculate_polygon_area": calculate_polygon_area,
    "find_adjacent_polygons": find_adjacent_polygons,
    "count_points_in_polygon": count_points_in_polygon,
    "multi_criteria_site_selection": multi_criteria_site_selection,
    "point_density_grid": point_density_grid,
    "line_density_grid": line_density_grid,
    "nearest_distance_to_features": nearest_distance_to_features,
    "nearest_neighbor_analysis": nearest_neighbor_analysis,
    "service_gap_analysis": service_gap_analysis,
    "multi_ring_service_analysis": multi_ring_service_analysis,
    "advanced_site_selection": advanced_site_selection,
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
