from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT, env_bool, env_float, env_int, env_str
from .data_cache import cache_key, restore_cached_layer, store_cached_layer
from .dataset_catalog import get_dataset_spec
from .errors import PermanentError, TransientError
from .http_client import download_file, request_json
from .gdal_runtime import configure_gdal_runtime


configure_gdal_runtime()


def _imports():
    import geopandas as gpd
    from shapely.geometry import LineString, Point, shape

    return gpd, LineString, Point, shape


def _write_layer(gdf, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    gdf.to_file(output, driver="GPKG")


def _source_columns(dataset_id: str) -> dict[str, str]:
    spec = get_dataset_spec(dataset_id)
    return {
        "dataset_id": dataset_id,
        "data_source": spec["source_id"],
        "data_license": spec["license"],
        "acquired_at": datetime.now(timezone.utc).isoformat(),
    }


def _request_overpass(spec: dict[str, Any], query: str, start_index: int = 0) -> dict:
    endpoints = [spec["endpoint"], *spec.get("fallback_endpoints", [])]
    errors = []
    for offset in range(len(endpoints)):
        endpoint = endpoints[(start_index + offset) % len(endpoints)]
        try:
            return request_json(
                endpoint,
                form={"data": query},
                timeout=env_int("OVERPASS_HTTP_TIMEOUT_SECONDS", 60),
                retries=0,
            )
        except TransientError as exc:
            errors.append(f"{endpoint}: {exc}")
    raise TransientError("All allowlisted Overpass endpoints failed: " + " | ".join(errors))


def _bounds_area_sq_km(gdf) -> float:
    if gdf.crs is None:
        raise PermanentError("Input geometry has no CRS.")
    projected = gdf.to_crs("EPSG:6933")
    bounds = projected.total_bounds
    return max(0.0, (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]) / 1_000_000)


def _validate_query_extent(gdf) -> None:
    area = _bounds_area_sq_km(gdf)
    limit = env_float("DATA_MAX_QUERY_AREA_SQ_KM", 15_000)
    if area > limit:
        raise PermanentError(
            f"Requested extent is {area:.1f} km2, exceeding limit {limit:.1f} km2."
        )


def download_region_boundary(params: dict[str, Any]) -> dict[str, Any]:
    gpd, _, _, shape = _imports()
    region_name = str(params["REGION_NAME"]).strip()
    output = Path(params["OUTPUT"])
    spec = get_dataset_spec("administrative_boundary")
    key = cache_key("boundary", {"region_name": region_name, "source": spec["source_id"]})
    if restore_cached_layer(key, output):
        cached = gpd.read_file(output)
        return {"output": str(output), "feature_count": len(cached), "cache_hit": True}

    response = request_json(
        spec["endpoint"],
        query={
            "q": region_name,
            "format": "geojson",
            "polygon_geojson": "1",
            "addressdetails": "1",
            "limit": "8",
            "accept-language": "zh-CN,zh",
        },
    )
    features = response.get("features", []) if isinstance(response, dict) else []
    polygon_features = [
        item for item in features
        if item.get("geometry", {}).get("type") in {"Polygon", "MultiPolygon"}
    ]
    if not polygon_features:
        raise PermanentError(f"No polygon boundary found for region: {region_name}")

    def rank(item: dict) -> tuple[int, float]:
        props = item.get("properties", {})
        importance = float(props.get("importance") or 0)
        exact = int(region_name in str(props.get("display_name", "")))
        return exact, importance

    selected = max(polygon_features, key=rank)
    geometry = shape(selected["geometry"])
    props = selected.get("properties", {})
    row = {
        "region_name": region_name,
        "display_name": str(props.get("display_name", region_name)),
        "osm_type": str(props.get("osm_type", "")),
        "osm_id": str(props.get("osm_id", "")),
        **_source_columns("administrative_boundary"),
        "geometry": geometry,
    }
    gdf = gpd.GeoDataFrame([row], geometry="geometry", crs="EPSG:4326")
    _validate_query_extent(gdf)
    _write_layer(gdf, output)
    store_cached_layer(key, output, {"region_name": region_name, **_source_columns("administrative_boundary")})
    return {"output": str(output), "feature_count": 1, "cache_hit": False}


def _overpass_bbox(gdf) -> tuple[float, float, float, float]:
    geographic = gdf.to_crs("EPSG:4326")
    west, south, east, north = geographic.total_bounds
    return south, west, north, east


def _overpass_query(
    filter_text: str,
    bbox: tuple[float, float, float, float],
    kind: str,
    *,
    distance_meters: float | None = None,
) -> str:
    south, west, north, east = bbox
    timeout = env_int("OVERPASS_QUERY_TIMEOUT_SECONDS", 120)
    bbox_text = f"{south:.7f},{west:.7f},{north:.7f},{east:.7f}"
    if kind == "poi":
        selectors = "\n".join(
            f"  {osm_type}{filter_text}({bbox_text});"
            for osm_type in ("node", "way", "relation")
        )
    return f"[out:json][timeout:{timeout}];\n(\n{selectors}\n);\nout center tags qt;"
    distance = float(distance_meters or 1000)
    poi_filter = get_dataset_spec("university_pois")["osm_filter"]
    selectors = "\n".join(
        f"  {osm_type}{poi_filter}({bbox_text});"
        for osm_type in ("node", "way", "relation")
    )
    return (
        f"[out:json][timeout:{timeout}];\n"
        f"(\n{selectors}\n)->.pois;\n"
        f"way{filter_text}(around.pois:{distance:.1f});\n"
        "out geom tags qt;"
    )


def _overpass_roads_in_bbox_query(
    bbox: tuple[float, float, float, float],
    filter_text: str,
) -> str:
    timeout = env_int("OVERPASS_QUERY_TIMEOUT_SECONDS", 120)
    south, west, north, east = bbox
    return (
        f"[out:json][timeout:{timeout}];\n"
        f"way{filter_text}({south:.7f},{west:.7f},{north:.7f},{east:.7f});\n"
        "out geom tags;"
    )


def _road_query_tiles(
    coordinates: list[tuple[float, float]],
    distance_meters: float,
) -> list[tuple[float, float, float, float]]:
    tile_degrees = env_float("OVERPASS_TILE_DEGREES", 0.2)
    groups: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for lon, lat in coordinates:
        key = (math.floor(lon / tile_degrees), math.floor(lat / tile_degrees))
        groups.setdefault(key, []).append((lon, lat))
    result = []
    for points in groups.values():
        lons = [point[0] for point in points]
        lats = [point[1] for point in points]
        center_lat = sum(lats) / len(lats)
        lat_padding = distance_meters / 110_574
        lon_padding = distance_meters / max(1, 111_320 * math.cos(math.radians(center_lat)))
        result.append((
            min(lats) - lat_padding,
            min(lons) - lon_padding,
            max(lats) + lat_padding,
            max(lons) + lon_padding,
        ))
    return sorted(result)


JIANGSU_CITY_TERMS = {
    "南京", "无锡", "徐州", "常州", "苏州", "南通", "连云港",
    "淮安", "盐城", "扬州", "镇江", "泰州", "宿迁", "江苏",
}

ROAD_CLASSES = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary",
    "primary_link", "secondary", "secondary_link", "tertiary",
    "tertiary_link", "unclassified", "residential", "living_street",
}


def _geofabrik_spec_for_region(region_name: str) -> dict[str, Any] | None:
    if any(term in region_name for term in JIANGSU_CITY_TERMS):
        return get_dataset_spec("road_network_jiangsu_extract")
    return None


def _download_roads_from_extract(
    region_name: str,
    area,
    output: Path,
) -> dict[str, Any] | None:
    configure_gdal_runtime()
    import geopandas as gpd

    spec = _geofabrik_spec_for_region(region_name)
    if spec is None:
        return None
    extract_path = PROJECT_ROOT / "outputs" / "data_cache" / "extracts" / "jiangsu-latest.osm.pbf"
    if env_bool("DATA_REFRESH_EXTRACTS", False) or not extract_path.exists():
        download_file(spec["endpoint"], extract_path)
    area_4326 = area.to_crs("EPSG:4326")
    west, south, east, north = area_4326.total_bounds
    try:
        roads = gpd.read_file(
            extract_path,
            layer="lines",
            bbox=(west, south, east, north),
        )
    except Exception as exc:
        raise PermanentError(f"Could not read Geofabrik OSM PBF lines: {exc}") from exc
    if "highway" not in roads.columns:
        raise PermanentError("Geofabrik OSM line layer has no highway field.")
    roads = roads[roads["highway"].isin(ROAD_CLASSES)].copy()
    if roads.empty:
        raise PermanentError("Geofabrik extract contains no supported roads in the analysis extent.")
    query_geometry = area_4326.geometry.union_all()
    if roads.crs is None:
        roads = roads.set_crs("EPSG:4326")
    elif str(roads.crs) != "EPSG:4326":
        roads = roads.to_crs("EPSG:4326")
    roads = roads[roads.geometry.intersects(query_geometry)].copy()
    if roads.empty:
        raise PermanentError("No Geofabrik roads intersect the university buffers.")
    if "osm_id" in roads.columns:
        roads["osm_key"] = "way:" + roads["osm_id"].astype(str)
        roads = roads.drop_duplicates(subset=["osm_key"])
    metadata = _source_columns("road_network_jiangsu_extract")
    for key, value in metadata.items():
        roads[key] = value
    keep = [
        column for column in (
            "osm_key", "osm_id", "name", "highway", "dataset_id", "data_source",
            "data_license", "acquired_at", "geometry",
        ) if column in roads.columns
    ]
    roads = roads[keep].copy()
    _write_layer(roads, output)
    return {
        "output": str(output),
        "feature_count": int(len(roads)),
        "cache_hit": False,
        "road_source": "geofabrik_jiangsu_pbf",
        "extract_file": str(extract_path),
    }


def download_osm_pois(params: dict[str, Any]) -> dict[str, Any]:
    gpd, _, Point, _ = _imports()
    boundary = gpd.read_file(params["BOUNDARY"])
    if boundary.empty or boundary.crs is None:
        raise PermanentError("Boundary layer is empty or has no CRS.")
    _validate_query_extent(boundary)
    poi_type = str(params.get("POI_TYPE", "university"))
    if poi_type != "university":
        raise PermanentError(f"Unsupported POI_TYPE: {poi_type}")
    output = Path(params["OUTPUT"])
    spec = get_dataset_spec("university_pois")
    bbox = _overpass_bbox(boundary)
    key = cache_key("poi", {"bbox": [round(v, 5) for v in bbox], "poi_type": poi_type})
    if restore_cached_layer(key, output):
        cached = gpd.read_file(output)
        return {"output": str(output), "feature_count": len(cached), "cache_hit": True}

    response = _request_overpass(spec, _overpass_query(spec["osm_filter"], bbox, "poi"))
    rows = []
    for element in response.get("elements", []):
        center = element.get("center", element)
        lon, lat = center.get("lon"), center.get("lat")
        if lon is None or lat is None:
            continue
        tags = element.get("tags", {})
        rows.append({
            "osm_key": f"{element.get('type')}:{element.get('id')}",
            "name": tags.get("name") or tags.get("name:zh") or "unnamed",
            "amenity": tags.get("amenity", ""),
            **_source_columns("university_pois"),
            "geometry": Point(float(lon), float(lat)),
        })
    if not rows:
        raise PermanentError("Overpass returned no university/college POIs.")
    pois = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    boundary_4326 = boundary.to_crs("EPSG:4326")
    region_geometry = boundary_4326.geometry.union_all()
    pois = pois[pois.geometry.intersects(region_geometry)].copy()
    pois = pois.drop_duplicates(subset=["osm_key"])
    max_features = env_int("DATA_MAX_FEATURES", 200_000)
    if len(pois) > max_features:
        raise PermanentError(f"POI result exceeds feature limit: {len(pois)}")
    if pois.empty:
        raise PermanentError("No university/college POIs fall inside the boundary.")
    _write_layer(pois, output)
    store_cached_layer(key, output, {"bbox": bbox, "poi_type": poi_type, "count": len(pois)})
    return {"output": str(output), "feature_count": len(pois), "cache_hit": False}


def download_osm_roads(params: dict[str, Any]) -> dict[str, Any]:
    gpd, LineString, _, _ = _imports()
    area = gpd.read_file(params["AREA"])
    if area.empty or area.crs is None:
        raise PermanentError("Road query area is empty or has no CRS.")
    _validate_query_extent(area)
    output = Path(params["OUTPUT"])
    region_name = str(params["REGION_NAME"]).strip()
    points = gpd.read_file(params["POINTS"])
    if points.empty or points.crs is None:
        raise PermanentError("Road query points are empty or have no CRS.")
    poi_type = str(params["POI_TYPE"])
    distance_meters = float(params["DISTANCE"])
    if poi_type != "university" or distance_meters <= 0:
        raise PermanentError("Road download requires university POIs and positive distance.")
    source_mode = env_str("ROAD_SOURCE_MODE", "auto").lower()
    if source_mode not in {"auto", "geofabrik", "overpass"}:
        raise PermanentError(f"Unsupported ROAD_SOURCE_MODE: {source_mode}")
    spec = get_dataset_spec("road_network")
    bbox = _overpass_bbox(area)
    points_4326 = points.to_crs("EPSG:4326")
    point_coordinates = [
        (round(float(geometry.x), 7), round(float(geometry.y), 7))
        for geometry in points_4326.geometry
        if geometry is not None and not geometry.is_empty
    ]
    max_batches = env_int("OVERPASS_MAX_BATCHES", 40)
    batches = _road_query_tiles(point_coordinates, distance_meters)
    if not batches:
        raise PermanentError("Road query contains no valid point coordinates.")
    if len(batches) > max_batches:
        raise PermanentError(
            f"Road query needs {len(batches)} batches; configured limit is {max_batches}."
        )
    key = cache_key(
        "roads",
        {
            "bbox": [round(v, 5) for v in bbox], "poi_type": poi_type,
            "distance": distance_meters, "points": point_coordinates, "source_mode": source_mode,
        },
    )
    if restore_cached_layer(key, output):
        cached = gpd.read_file(output)
        return {"output": str(output), "feature_count": len(cached), "cache_hit": True}

    if source_mode in {"auto", "geofabrik"}:
        try:
            extract_result = _download_roads_from_extract(region_name, area, output)
        except (PermanentError, TransientError):
            if source_mode == "geofabrik":
                raise
            extract_result = None
        if extract_result is not None:
            store_cached_layer(
                key, output,
                {"region_name": region_name, "distance": distance_meters, **extract_result},
            )
            return extract_result

    elements_by_id: dict[int, dict[str, Any]] = {}
    for batch_index, batch_bbox in enumerate(batches):
        response = _request_overpass(
            spec,
            _overpass_roads_in_bbox_query(batch_bbox, spec["osm_filter"]),
            start_index=batch_index,
        )
        for element in response.get("elements", []):
            if element.get("type") == "way" and element.get("id") is not None:
                elements_by_id[int(element["id"])] = element
        if batch_index + 1 < len(batches):
            time.sleep(env_float("OVERPASS_BATCH_DELAY_SECONDS", 0.5))
    rows = []
    for element in elements_by_id.values():
        line_coordinates = [
            (float(point["lon"]), float(point["lat"]))
            for point in element.get("geometry", [])
            if "lon" in point and "lat" in point
        ]
        if len(line_coordinates) < 2:
            continue
        tags = element.get("tags", {})
        rows.append({
            "osm_key": f"way:{element.get('id')}",
            "name": tags.get("name", ""),
            "highway": tags.get("highway", ""),
            **_source_columns("road_network"),
            "geometry": LineString(line_coordinates),
        })
    if not rows:
        raise PermanentError("Overpass returned no road features.")
    roads = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    roads = roads.drop_duplicates(subset=["osm_key"])
    area_4326 = area.to_crs("EPSG:4326")
    query_geometry = area_4326.geometry.union_all()
    roads = roads[roads.geometry.intersects(query_geometry)].copy()
    max_features = env_int("DATA_MAX_FEATURES", 200_000)
    if len(roads) > max_features:
        raise PermanentError(f"Road result exceeds feature limit: {len(roads)}")
    if roads.empty:
        raise PermanentError("No roads intersect the requested analysis area.")
    _write_layer(roads, output)
    store_cached_layer(
        key, output,
        {
            "bbox": bbox, "poi_type": poi_type, "distance": distance_meters,
            "point_count": len(point_coordinates), "batch_count": len(batches), "count": len(roads),
        },
    )
    return {
        "output": str(output), "feature_count": len(roads), "cache_hit": False,
        "point_count": len(point_coordinates), "batch_count": len(batches),
    }


DATA_ACQUISITION_HANDLERS = {
    "download_region_boundary": download_region_boundary,
    "download_osm_pois": download_osm_pois,
    "download_osm_roads": download_osm_roads,
}
