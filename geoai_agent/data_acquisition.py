from __future__ import annotations

import math
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT, env_bool, env_float, env_int, env_str
from .data_cache import (
    cache_key,
    restore_cached_json,
    restore_cached_layer,
    store_cached_json,
    store_cached_layer,
)
from .dataset_catalog import POI_FILTERS, POI_LABELS, get_dataset_spec
from .errors import BudgetExceededError, PermanentError, TransientError
from .http_client import download_file, request_json
from .gdal_runtime import configure_gdal_runtime
from .progress import append_progress


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


def _copy_layer_file(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == output.resolve():
        return
    temporary = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.tmp.gpkg")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def _source_columns(dataset_id: str) -> dict[str, str]:
    spec = get_dataset_spec(dataset_id)
    return {
        "dataset_id": dataset_id,
        "data_source": spec["source_id"],
        "data_license": spec["license"],
        "acquired_at": datetime.now(timezone.utc).isoformat(),
    }


def _request_overpass(
    spec: dict[str, Any],
    query: str,
    start_index: int = 0,
    *,
    timeout_seconds: int | None = None,
    max_endpoints: int | None = None,
    rotate_endpoints: bool = False,
) -> dict:
    primary = spec["endpoint"]
    fallbacks = list(spec.get("fallback_endpoints", []))
    if rotate_endpoints:
        endpoints = [primary, *fallbacks]
        if endpoints:
            offset = start_index % len(endpoints)
            endpoints = endpoints[offset:] + endpoints[:offset]
    elif fallbacks:
        fallback_start = start_index % len(fallbacks)
        fallbacks = fallbacks[fallback_start:] + fallbacks[:fallback_start]
        # Road requests keep the primary endpoint first. High-volume tiled POI
        # requests rotate all allowlisted endpoints to avoid concentrating load.
        endpoints = [primary, *fallbacks]
    else:
        endpoints = [primary]
    attempts = len(endpoints) if max_endpoints is None else min(len(endpoints), max_endpoints)
    errors = []
    for offset in range(attempts):
        endpoint = endpoints[offset]
        try:
            return request_json(
                endpoint,
                form={"data": query},
                timeout=timeout_seconds or env_int("OVERPASS_HTTP_TIMEOUT_SECONDS", 60),
                retries=0,
            )
        except TransientError as exc:
            errors.append(f"{endpoint}: {exc}")
    raise TransientError("All allowlisted Overpass endpoints failed: " + " | ".join(errors))


def _road_progress(params: dict[str, Any], payload: dict[str, Any]) -> None:
    task_id = str(params.get("__TASK_ID") or "")
    if task_id:
        append_progress(task_id, {"node": "road_download", **payload})


def _poi_progress(params: dict[str, Any], payload: dict[str, Any]) -> None:
    task_id = str(params.get("__TASK_ID") or "")
    if task_id:
        append_progress(task_id, {"node": "poi_download", **payload})


def _water_progress(params: dict[str, Any], payload: dict[str, Any]) -> None:
    task_id = str(params.get("__TASK_ID") or "")
    if task_id:
        append_progress(task_id, {"node": "water_download", **payload})


def _road_request_settings() -> tuple[float, int, int]:
    return (
        max(10.0, env_float("ROAD_DOWNLOAD_DEADLINE_SECONDS", 120.0)),
        max(3, env_int("ROAD_HTTP_TIMEOUT_SECONDS", 20)),
        max(1, env_int("ROAD_MAX_ENDPOINT_ATTEMPTS", 2)),
    )


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

    source_mode = env_str("BOUNDARY_SOURCE_MODE", "auto").lower()
    if source_mode not in {"auto", "local_pbf", "nominatim"}:
        raise PermanentError(f"Unsupported BOUNDARY_SOURCE_MODE: {source_mode}")
    if source_mode in {"auto", "local_pbf"}:
        try:
            local_result = _download_region_boundary_from_local_pbf(
                region_name,
                output,
            )
        except PermanentError:
            if source_mode == "local_pbf":
                raise
            local_result = None
        if local_result is not None:
            store_cached_layer(
                key,
                output,
                {"region_name": region_name, **local_result},
            )
            return local_result
        if source_mode == "local_pbf":
            raise PermanentError(
                f"Local OSM snapshot has no administrative boundary for: {region_name}"
            )

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
    filter_text: str | list[str],
    bbox: tuple[float, float, float, float],
    kind: str,
    *,
    distance_meters: float | None = None,
    osm_types: tuple[str, ...] = ("node", "way", "relation"),
    query_timeout_seconds: int | None = None,
) -> str:
    south, west, north, east = bbox
    timeout = query_timeout_seconds or env_int("OVERPASS_QUERY_TIMEOUT_SECONDS", 120)
    bbox_text = f"{south:.7f},{west:.7f},{north:.7f},{east:.7f}"
    if kind == "poi":
        filters = [filter_text] if isinstance(filter_text, str) else filter_text
        selectors = "\n".join(
            f"  {osm_type}{item}({bbox_text});"
            for item in filters
            for osm_type in osm_types
        )
        return f"[out:json][timeout:{timeout}];\n(\n{selectors}\n);\nout center tags qt;"
    raise PermanentError(f"Unsupported Overpass query kind: {kind}")


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
    *,
    tile_degrees: float | None = None,
) -> list[tuple[float, float, float, float]]:
    tile_degrees = tile_degrees or env_float("OVERPASS_TILE_DEGREES", 0.2)
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

MAIN_ROAD_CLASSES = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
    "secondary", "secondary_link",
}


def _road_filter(classes: set[str]) -> str:
    values = "|".join(sorted(classes))
    return f'["highway"~"^({values})$"]'


def _split_bbox(
    bbox: tuple[float, float, float, float],
) -> list[tuple[float, float, float, float]]:
    south, west, north, east = bbox
    middle_lat = (south + north) / 2
    middle_lon = (west + east) / 2
    return [
        (south, west, middle_lat, middle_lon),
        (south, middle_lon, middle_lat, east),
        (middle_lat, west, north, middle_lon),
        (middle_lat, middle_lon, north, east),
    ]


def _compact_road_error(exc: BaseException, limit: int = 280) -> str:
    text = " ".join(str(exc).split())
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def _request_road_elements(
    params: dict[str, Any],
    spec: dict[str, Any],
    bbox: tuple[float, float, float, float],
    road_filter: str,
    *,
    batch_label: int | str,
    total_batches: int,
    start_index: int,
    started_at: float,
    deadline_seconds: float,
    request_timeout: int,
    endpoint_attempts: int,
    depth: int = 0,
) -> list[dict[str, Any]]:
    elapsed = time.monotonic() - started_at
    remaining = deadline_seconds - elapsed
    if remaining <= 0:
        raise BudgetExceededError(
            f"Road download exceeded the {deadline_seconds:.0f}s interactive deadline."
        )
    _road_progress(
        params,
        {
            "status": "running",
            "source": "overpass",
            "batch": batch_label,
            "total_batches": total_batches,
            "retry_depth": depth,
            "elapsed_seconds": round(elapsed, 1),
        },
    )
    try:
        response = _request_overpass(
            spec,
            _overpass_roads_in_bbox_query(bbox, road_filter),
            start_index=start_index,
            timeout_seconds=max(1, min(request_timeout, int(remaining))),
            max_endpoints=endpoint_attempts,
        )
    except TransientError as exc:
        max_depth = max(0, env_int("ROAD_SPLIT_RETRY_DEPTH", 1))
        south, west, north, east = bbox
        min_span = max(0.001, env_float("ROAD_MIN_SPLIT_DEGREES", 0.005))
        can_split = (
            depth < max_depth
            and (north - south) > min_span
            and (east - west) > min_span
        )
        if not can_split:
            concise = _compact_road_error(exc)
            _road_progress(
                params,
                {
                    "status": "failed",
                    "source": "overpass",
                    "batch": batch_label,
                    "total_batches": total_batches,
                    "retry_depth": depth,
                    "error": concise,
                },
            )
            raise TransientError(
                f"Road batch {batch_label} failed after bounded retries: {concise}"
            ) from exc
        _road_progress(
            params,
            {
                "status": "retrying",
                "source": "overpass",
                "batch": batch_label,
                "total_batches": total_batches,
                "retry_depth": depth,
                "reason": _compact_road_error(exc),
                "strategy": "split_bbox_into_4",
            },
        )
        elements: list[dict[str, Any]] = []
        for child_index, child_bbox in enumerate(_split_bbox(bbox), start=1):
            elements.extend(
                _request_road_elements(
                    params,
                    spec,
                    child_bbox,
                    road_filter,
                    batch_label=f"{batch_label}.{child_index}",
                    total_batches=total_batches,
                    start_index=start_index + child_index,
                    started_at=started_at,
                    deadline_seconds=deadline_seconds,
                    request_timeout=request_timeout,
                    endpoint_attempts=endpoint_attempts,
                    depth=depth + 1,
                )
            )
        return elements
    elements = response.get("elements", [])
    _road_progress(
        params,
        {
            "status": "success",
            "source": "overpass",
            "batch": batch_label,
            "total_batches": total_batches,
            "retry_depth": depth,
            "road_elements": len(elements),
        },
    )
    return elements


def _geofabrik_spec_for_region(region_name: str) -> dict[str, Any] | None:
    if any(term in region_name for term in JIANGSU_CITY_TERMS):
        return get_dataset_spec("road_network_jiangsu_extract")
    return None


def _geofabrik_extract_path() -> Path:
    configured = env_str("OSM_LOCAL_PBF_PATH", "")
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else PROJECT_ROOT / path
    candidates = [
        PROJECT_ROOT / "data" / "osm" / "jiangsu-latest.osm.pbf",
        PROJECT_ROOT / "outputs" / "data_cache" / "extracts" / "jiangsu-latest.osm.pbf",
    ]
    return next((path for path in candidates if path.exists()), candidates[0])


def _offline_pack_path(layer_name: str) -> Path:
    return PROJECT_ROOT / "data" / "osm" / "nanjing" / f"{layer_name}.gpkg"


def _offline_pack_covers(area) -> bool:
    boundary_path = _offline_pack_path("boundary")
    if not boundary_path.exists() or area.empty or area.crs is None:
        return False
    import geopandas as gpd

    pack_boundary = gpd.read_file(boundary_path).to_crs("EPSG:4326")
    area_4326 = area.to_crs("EPSG:4326")
    if pack_boundary.empty:
        return False
    pack_bounds = pack_boundary.total_bounds
    area_bounds = area_4326.total_bounds
    tolerance = 0.02
    return (
        area_bounds[0] >= pack_bounds[0] - tolerance
        and area_bounds[1] >= pack_bounds[1] - tolerance
        and area_bounds[2] <= pack_bounds[2] + tolerance
        and area_bounds[3] <= pack_bounds[3] + tolerance
    )


def _offline_pack_is_full_area(area) -> bool:
    boundary_path = _offline_pack_path("boundary")
    if not boundary_path.exists() or area.empty or area.crs is None:
        return False
    import geopandas as gpd

    pack = gpd.read_file(boundary_path).to_crs("EPSG:6933")
    candidate = area.to_crs("EPSG:6933")
    pack_area = float(pack.geometry.area.sum())
    candidate_area = float(candidate.geometry.area.sum())
    if pack_area <= 0:
        return False
    return 0.98 <= candidate_area / pack_area <= 1.02


def _local_osm_snapshot_metadata(dataset_id: str, extract_path: Path) -> dict[str, Any]:
    spec = get_dataset_spec(dataset_id)
    snapshot_time = datetime.fromtimestamp(
        extract_path.stat().st_mtime,
        tz=timezone.utc,
    ).isoformat()
    return {
        "dataset_id": dataset_id,
        "data_source": "osm_local_pbf_snapshot",
        "data_license": spec["license"],
        "acquired_at": snapshot_time,
        "snapshot_file": extract_path.name,
        "snapshot_modified_at": snapshot_time,
    }


def _escape_ogr_text(value: str) -> str:
    return value.replace("'", "''")


def _region_name_candidates(region_name: str) -> list[str]:
    name = region_name.strip()
    candidates = [name]
    if name.endswith("市"):
        candidates.append(name[:-1])
    elif name and name != "江苏":
        candidates.append(name + "市")
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def _download_region_boundary_from_local_pbf(
    region_name: str,
    output: Path,
) -> dict[str, Any] | None:
    if _geofabrik_spec_for_region(region_name) is None:
        return None
    extract_path = _geofabrik_extract_path()
    if not extract_path.exists():
        return None
    configure_gdal_runtime()
    import geopandas as gpd

    packed_boundary = _offline_pack_path("boundary")
    if (
        not env_bool("OSM_OFFLINE_PACK_BUILDING", False)
        and packed_boundary.exists()
        and any(
        name in {"南京", "南京市"} for name in _region_name_candidates(region_name)
        )
    ):
        packed = gpd.read_file(packed_boundary)
        if not packed.empty:
            _validate_query_extent(packed)
            _copy_layer_file(packed_boundary, output)
            snapshot_time = _local_osm_snapshot_metadata(
                "administrative_boundary", extract_path
            )["snapshot_modified_at"]
            return {
                "output": str(output),
                "feature_count": 1,
                "cache_hit": False,
                "data_source": "osm_offline_normalized_pack",
                "snapshot_file": str(extract_path),
                "snapshot_modified_at": snapshot_time,
                "offline_pack_hit": True,
            }

    names = _region_name_candidates(region_name)
    name_clause = " OR ".join(
        f"name = '{_escape_ogr_text(name)}'" for name in names
    )
    try:
        boundaries = gpd.read_file(
            extract_path,
            layer="multipolygons",
            where=f"boundary = 'administrative' AND ({name_clause})",
            columns=[
                "osm_id", "name", "admin_level", "boundary", "place", "other_tags",
            ],
        )
    except Exception as exc:
        raise PermanentError(f"Could not read local OSM administrative boundary: {exc}") from exc
    if boundaries.empty:
        return None
    if boundaries.crs is None:
        boundaries = boundaries.set_crs("EPSG:4326")
    elif str(boundaries.crs) != "EPSG:4326":
        boundaries = boundaries.to_crs("EPSG:4326")
    boundaries["_exact"] = boundaries["name"].astype(str).isin(names).astype(int)
    boundaries["_level"] = boundaries["admin_level"].fillna("99").astype(str)
    boundaries["_level"] = boundaries["_level"].map(
        lambda value: int(value) if value.isdigit() else 99
    )
    selected = boundaries.sort_values(["_exact", "_level"], ascending=[False, True]).iloc[[0]].copy()
    metadata = _local_osm_snapshot_metadata("administrative_boundary", extract_path)
    selected["region_name"] = region_name
    selected["display_name"] = selected["name"].fillna(region_name)
    selected["osm_type"] = "relation"
    for column, value in metadata.items():
        selected[column] = value
    selected = selected[[
        "region_name", "display_name", "osm_type", "osm_id", "dataset_id",
        "data_source", "data_license", "acquired_at", "snapshot_file",
        "snapshot_modified_at", "geometry",
    ]]
    _validate_query_extent(selected)
    _write_layer(selected, output)
    return {
        "output": str(output),
        "feature_count": 1,
        "cache_hit": False,
        "data_source": "osm_local_pbf_snapshot",
        "snapshot_file": str(extract_path),
        "snapshot_modified_at": metadata["snapshot_modified_at"],
    }


def _hstore_has(tags, key: str, values: set[str]):
    mask = tags.fillna("").astype(str).map(lambda _: False)
    for value in values:
        marker = f'"{key}"=>"{value}"'
        mask = mask | tags.fillna("").astype(str).str.contains(marker, regex=False)
    return mask


def _hstore_value(raw: Any, key: str) -> str:
    text = str(raw or "")
    marker = f'"{key}"=>"'
    start = text.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    end = text.find('"', start)
    return text[start:end] if end >= start else ""


POI_LOCAL_TAGS: dict[str, tuple[str, set[str]]] = {
    "university": ("amenity", {"university", "college"}),
    "school": ("amenity", {"school"}),
    "hospital": ("amenity", {"hospital"}),
    "clinic": ("amenity", {"clinic", "doctors"}),
    "pharmacy": ("amenity", {"pharmacy"}),
    "park": ("leisure", {"park"}),
    "police": ("amenity", {"police"}),
    "fire_station": ("amenity", {"fire_station"}),
    "supermarket": ("shop", {"supermarket"}),
    "charging_station": ("amenity", {"charging_station"}),
}


def _download_pois_from_local_pbf(
    boundary,
    poi_type: str,
    output: Path,
) -> dict[str, Any] | None:
    extract_path = _geofabrik_extract_path()
    if not extract_path.exists():
        return None
    configure_gdal_runtime()
    import geopandas as gpd
    import pandas as pd

    boundary_4326 = boundary.to_crs("EPSG:4326")
    packed_pois = _offline_pack_path(f"poi_{poi_type}")
    if (
        not env_bool("OSM_OFFLINE_PACK_BUILDING", False)
        and packed_pois.exists()
        and _offline_pack_covers(boundary_4326)
    ):
        pois = gpd.read_file(packed_pois).to_crs("EPSG:4326")
        if _offline_pack_is_full_area(boundary_4326):
            _copy_layer_file(packed_pois, output)
        else:
            west, south, east, north = boundary_4326.total_bounds
            pois = pois.cx[west:east, south:north].copy()
            region_geometry = boundary_4326.geometry.make_valid().union_all()
            pois = pois[pois.geometry.intersects(region_geometry)].copy()
            if not pois.empty:
                _write_layer(pois, output)
        if not pois.empty:
            return {
                "output": str(output),
                "feature_count": int(len(pois)),
                "cache_hit": False,
                "data_source": "osm_offline_normalized_pack",
                "snapshot_file": str(extract_path),
                "snapshot_modified_at": _local_osm_snapshot_metadata(
                    "osm_pois", extract_path
                )["snapshot_modified_at"],
                "partial_tiles": False,
                "offline_pack_hit": True,
            }
    west, south, east, north = boundary_4326.total_bounds
    try:
        points = gpd.read_file(
            extract_path,
            layer="points",
            bbox=(west, south, east, north),
            columns=["osm_id", "name", "other_tags"],
        )
    except Exception as exc:
        raise PermanentError(f"Could not read local OSM POI points: {exc}") from exc
    tags = points.get("other_tags", pd.Series("", index=points.index)).fillna("")
    if poi_type == "subway_station":
        point_mask = (
            _hstore_has(tags, "railway", {"station"})
            & _hstore_has(tags, "station", {"subway"})
        ) | (
            _hstore_has(tags, "public_transport", {"station"})
            & _hstore_has(tags, "subway", {"yes"})
        )
    else:
        key, values = POI_LOCAL_TAGS[poi_type]
        point_mask = _hstore_has(tags, key, values)
    points = points[point_mask].copy()
    if not points.empty:
        points["osm_key"] = "node:" + points["osm_id"].astype(str)

    frames = [points]
    if poi_type != "subway_station":
        field, values = POI_LOCAL_TAGS[poi_type]
        where = " OR ".join(
            f"{field} = '{_escape_ogr_text(value)}'" for value in sorted(values)
        )
        try:
            areas = gpd.read_file(
                extract_path,
                layer="multipolygons",
                bbox=(west, south, east, north),
                where=f"({where})",
                columns=[
                    "osm_id", "osm_way_id", "name", "amenity", "leisure", "shop",
                    "other_tags",
                ],
            )
        except Exception as exc:
            raise PermanentError(f"Could not read local OSM POI areas: {exc}") from exc
        if not areas.empty:
            area_ids = areas["osm_id"].fillna(areas["osm_way_id"]).astype(str)
            areas["osm_key"] = "area:" + area_ids
            areas["geometry"] = areas.geometry.representative_point()
            frames.append(areas)

    non_empty = [frame for frame in frames if not frame.empty]
    if not non_empty:
        raise PermanentError(f"Local OSM snapshot contains no {POI_LABELS[poi_type]} POIs.")
    pois = gpd.GeoDataFrame(pd.concat(non_empty, ignore_index=True), crs="EPSG:4326")
    region_geometry = boundary_4326.geometry.make_valid().union_all()
    pois = pois[pois.geometry.intersects(region_geometry)].copy()
    pois = pois.drop_duplicates(subset=["osm_key"])
    if pois.empty:
        raise PermanentError(f"No local {POI_LABELS[poi_type]} POIs fall inside the boundary.")
    raw_tags = pois.get("other_tags", pd.Series("", index=pois.index)).fillna("")
    for field in ("amenity", "railway", "shop", "leisure"):
        existing = pois[field].fillna("").astype(str) if field in pois.columns else pd.Series("", index=pois.index)
        pois[field] = [
            value or _hstore_value(tag_text, field)
            for value, tag_text in zip(existing, raw_tags)
        ]
    pois["name"] = pois.get("name", "").fillna("unnamed").replace("", "unnamed")
    pois["poi_type"] = poi_type
    pois["poi_label"] = POI_LABELS[poi_type]
    metadata = _local_osm_snapshot_metadata("osm_pois", extract_path)
    for column, value in metadata.items():
        pois[column] = value
    pois["tile_download_status"] = "local_snapshot"
    pois["tiles_requested"] = 0
    pois["tiles_failed"] = 0
    max_features = env_int("DATA_MAX_FEATURES", 200_000)
    if len(pois) > max_features:
        raise PermanentError(f"Local POI result exceeds feature limit: {len(pois)}")
    keep = [
        "osm_key", "name", "amenity", "railway", "shop", "leisure", "poi_type",
        "poi_label", "dataset_id", "data_source", "data_license", "acquired_at",
        "snapshot_file", "snapshot_modified_at", "tile_download_status",
        "tiles_requested", "tiles_failed", "geometry",
    ]
    pois = pois[[column for column in keep if column in pois.columns]].copy()
    _write_layer(pois, output)
    return {
        "output": str(output),
        "feature_count": int(len(pois)),
        "cache_hit": False,
        "data_source": "osm_local_pbf_snapshot",
        "snapshot_file": str(extract_path),
        "snapshot_modified_at": metadata["snapshot_modified_at"],
        "partial_tiles": False,
    }


def _download_water_from_local_pbf(
    boundary,
    output: Path,
) -> dict[str, Any] | None:
    extract_path = _geofabrik_extract_path()
    if not extract_path.exists():
        return None
    configure_gdal_runtime()
    import geopandas as gpd

    boundary_4326 = boundary.to_crs("EPSG:4326")
    packed_water = _offline_pack_path("water")
    if (
        not env_bool("OSM_OFFLINE_PACK_BUILDING", False)
        and packed_water.exists()
        and _offline_pack_covers(boundary_4326)
    ):
        waters = gpd.read_file(packed_water).to_crs("EPSG:4326")
        if _offline_pack_is_full_area(boundary_4326):
            _copy_layer_file(packed_water, output)
        else:
            west, south, east, north = boundary_4326.total_bounds
            waters = waters.cx[west:east, south:north].copy()
            region = boundary_4326.geometry.make_valid().union_all()
            waters = waters[waters.geometry.intersects(region)].copy()
            waters = waters[~waters.geometry.is_empty].copy()
            if not waters.empty:
                _write_layer(waters, output)
        if not waters.empty:
            return {
                "output": str(output),
                "feature_count": int(len(waters)),
                "cache_hit": False,
                "data_source": "osm_offline_normalized_pack",
                "snapshot_file": str(extract_path),
                "snapshot_modified_at": _local_osm_snapshot_metadata(
                    "water_areas", extract_path
                )["snapshot_modified_at"],
                "partial_tiles": False,
                "offline_pack_hit": True,
            }
    west, south, east, north = boundary_4326.total_bounds
    where = "natural = 'water' OR landuse = 'reservoir' OR landuse = 'basin'"
    try:
        waters = gpd.read_file(
            extract_path,
            layer="multipolygons",
            bbox=(west, south, east, north),
            where=where,
            columns=[
                "osm_id", "osm_way_id", "name", "landuse", "natural", "other_tags",
            ],
        )
    except Exception as exc:
        raise PermanentError(f"Could not read local OSM water polygons: {exc}") from exc
    if waters.empty:
        raise PermanentError("Local OSM snapshot contains no water polygons in the area.")
    invalid = ~waters.geometry.is_valid
    if invalid.any():
        waters.loc[invalid, "geometry"] = waters.loc[invalid].geometry.make_valid()
    waters = waters[~waters.geometry.is_empty].copy()
    region = boundary_4326.geometry.make_valid().union_all()
    waters = waters[waters.geometry.intersects(region)].copy()
    waters = waters[~waters.geometry.is_empty].copy()
    if waters.empty:
        raise PermanentError("No local water polygons intersect the boundary.")
    area_ids = waters["osm_id"].fillna(waters["osm_way_id"]).astype(str)
    waters["osm_key"] = "area:" + area_ids
    waters["water_type"] = waters["natural"].fillna(waters["landuse"]).fillna("water")
    metadata = _local_osm_snapshot_metadata("water_areas", extract_path)
    for column, value in metadata.items():
        waters[column] = value
    waters = waters.drop_duplicates(subset=["osm_key"])
    keep = [
        "osm_key", "name", "water_type", "dataset_id", "data_source", "data_license",
        "acquired_at", "snapshot_file", "snapshot_modified_at", "geometry",
    ]
    waters = waters[[column for column in keep if column in waters.columns]].copy()
    _write_layer(waters, output)
    return {
        "output": str(output),
        "feature_count": int(len(waters)),
        "cache_hit": False,
        "data_source": "osm_local_pbf_snapshot",
        "snapshot_file": str(extract_path),
        "snapshot_modified_at": metadata["snapshot_modified_at"],
        "partial_tiles": False,
    }


def _should_try_geofabrik(source_mode: str) -> bool:
    """In auto mode, never surprise the user with a full province download."""
    return (
        source_mode == "geofabrik"
        or env_bool("DATA_REFRESH_EXTRACTS", False)
        or _geofabrik_extract_path().exists()
    )


def _download_roads_from_extract(
    region_name: str,
    area,
    output: Path,
    road_classes: set[str] | None = None,
) -> dict[str, Any] | None:
    configure_gdal_runtime()
    import geopandas as gpd

    spec = _geofabrik_spec_for_region(region_name)
    if spec is None:
        return None
    extract_path = _geofabrik_extract_path()
    if env_bool("DATA_REFRESH_EXTRACTS", False) or not extract_path.exists():
        download_file(spec["endpoint"], extract_path)
    area_4326 = area.to_crs("EPSG:4326")
    requested_classes = road_classes or ROAD_CLASSES
    packed_roads = _offline_pack_path("main_roads")
    if (
        not env_bool("OSM_OFFLINE_PACK_BUILDING", False)
        and
        packed_roads.exists()
        and requested_classes.issubset(MAIN_ROAD_CLASSES)
        and _offline_pack_covers(area_4326)
    ):
        roads = gpd.read_file(packed_roads).to_crs("EPSG:4326")
        roads = roads[roads["highway"].isin(requested_classes)].copy()
        if _offline_pack_is_full_area(area_4326):
            _copy_layer_file(packed_roads, output)
        else:
            west, south, east, north = area_4326.total_bounds
            roads = roads.cx[west:east, south:north].copy()
            query_geometry = area_4326.geometry.make_valid().union_all()
            roads = roads[roads.geometry.intersects(query_geometry)].copy()
            if not roads.empty:
                _write_layer(roads, output)
        if not roads.empty:
            metadata = _local_osm_snapshot_metadata(
                "road_network_jiangsu_extract", extract_path
            )
            return {
                "output": str(output),
                "feature_count": int(len(roads)),
                "cache_hit": False,
                "road_source": "osm_offline_normalized_pack",
                "extract_file": str(extract_path),
                "snapshot_modified_at": metadata["snapshot_modified_at"],
                "road_level": "main",
                "offline_pack_hit": True,
            }
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
    roads = roads[roads["highway"].isin(requested_classes)].copy()
    if roads.empty:
        raise PermanentError("Geofabrik extract contains no supported roads in the analysis extent.")
    query_geometry = area_4326.geometry.make_valid().union_all()
    if roads.crs is None:
        roads = roads.set_crs("EPSG:4326")
    elif str(roads.crs) != "EPSG:4326":
        roads = roads.to_crs("EPSG:4326")
    roads = roads[roads.geometry.intersects(query_geometry)].copy()
    if roads.empty:
        raise PermanentError("No local OSM roads intersect the analysis area.")
    if "osm_id" in roads.columns:
        roads["osm_key"] = "way:" + roads["osm_id"].astype(str)
        roads = roads.drop_duplicates(subset=["osm_key"])
    metadata = _local_osm_snapshot_metadata("road_network_jiangsu_extract", extract_path)
    for key, value in metadata.items():
        roads[key] = value
    road_level = "main" if requested_classes == MAIN_ROAD_CLASSES else "all"
    roads["road_level"] = road_level
    keep = [
        column for column in (
            "osm_key", "osm_id", "name", "highway", "dataset_id", "data_source",
            "data_license", "acquired_at", "snapshot_file", "snapshot_modified_at",
            "road_level", "geometry",
        ) if column in roads.columns
    ]
    roads = roads[keep].copy()
    _write_layer(roads, output)
    return {
        "output": str(output),
        "feature_count": int(len(roads)),
        "cache_hit": False,
        "road_source": "osm_local_pbf_snapshot",
        "extract_file": str(extract_path),
        "snapshot_modified_at": metadata["snapshot_modified_at"],
        "road_level": road_level,
    }


def _slippy_tile_index(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    scale = 2 ** zoom
    safe_lat = max(-85.05112878, min(85.05112878, lat))
    x = int((lon + 180.0) / 360.0 * scale)
    lat_rad = math.radians(safe_lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * scale)
    return max(0, min(scale - 1, x)), max(0, min(scale - 1, y))


def _vector_tile_bounds_3857(zoom: int, x: int, y: int):
    from shapely.geometry import box

    world = 20_037_508.342789244
    span = (2 * world) / (2 ** zoom)
    west = -world + x * span
    east = west + span
    north = world - y * span
    south = north - span
    return box(west, south, east, north)


def _vector_tiles_for_area(area, zoom: int) -> list[tuple[int, int, Any]]:
    area_4326 = area.to_crs("EPSG:4326")
    west, south, east, north = area_4326.total_bounds
    min_x, min_y = _slippy_tile_index(west, north, zoom)
    max_x, max_y = _slippy_tile_index(east, south, zoom)
    area_3857 = area.to_crs("EPSG:3857")
    geometry = area_3857.geometry.union_all()
    tiles = []
    for x in range(min_x, max_x + 1):
        for y in range(min_y, max_y + 1):
            bounds = _vector_tile_bounds_3857(zoom, x, y)
            if geometry.intersects(bounds):
                tiles.append((x, y, bounds))
    return tiles


def _vector_tile_batch_is_usable(success_count: int, total_count: int) -> bool:
    """Accept bounded partial coverage instead of discarding every good tile."""
    if success_count <= 0 or total_count <= 0:
        return False
    minimum_ratio = max(
        0.0,
        min(1.0, env_float("ROAD_VECTOR_TILE_MIN_SUCCESS_RATIO", 0.75)),
    )
    required = max(1, math.ceil(total_count * minimum_ratio))
    return success_count >= required


def _degree_tiles_for_area(
    area,
    tile_degrees: float,
) -> list[tuple[float, float, float, float]]:
    """Return only degree-grid cells that intersect the requested geometry."""
    from shapely.geometry import box

    tile_degrees = max(0.05, min(2.0, float(tile_degrees)))
    geographic = area.to_crs("EPSG:4326")
    west, south, east, north = geographic.total_bounds
    geometry = geographic.geometry.union_all()
    start_lon = math.floor(west / tile_degrees) * tile_degrees
    start_lat = math.floor(south / tile_degrees) * tile_degrees
    tiles: list[tuple[float, float, float, float]] = []
    lon = start_lon
    while lon < east:
        lat = start_lat
        while lat < north:
            candidate = box(lon, lat, lon + tile_degrees, lat + tile_degrees)
            if geometry.intersects(candidate):
                tiles.append((lat, lon, lat + tile_degrees, lon + tile_degrees))
            lat += tile_degrees
        lon += tile_degrees
    return tiles


def _poi_tile_batch_is_usable(success_count: int, total_count: int) -> bool:
    if success_count <= 0 or total_count <= 0:
        return False
    minimum_ratio = max(
        0.0,
        min(1.0, env_float("POI_TILE_MIN_SUCCESS_RATIO", 0.75)),
    )
    return success_count >= max(1, math.ceil(total_count * minimum_ratio))


def _download_roads_from_vector_tiles(
    area,
    output: Path,
    road_classes: set[str],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Use official OSM Shortbread tiles when Overpass is unavailable."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    import geopandas as gpd
    import pandas as pd

    zoom = max(5, min(14, env_int("ROAD_VECTOR_TILE_ZOOM", 11)))
    tiles = _vector_tiles_for_area(area, zoom)
    max_tiles = max(1, env_int("ROAD_VECTOR_TILE_MAX_TILES", 120))
    if not tiles:
        raise PermanentError("No OSM vector tiles intersect the road analysis area.")
    if len(tiles) > max_tiles:
        raise BudgetExceededError(
            f"Road vector fallback needs {len(tiles)} tiles; configured limit is {max_tiles}."
        )
    spec = get_dataset_spec("road_vector_tiles")
    cache_root = (
        PROJECT_ROOT / "outputs" / "data_cache" / "vector_tiles" / "shortbread_v1"
    )
    timeout = max(3, env_int("ROAD_VECTOR_TILE_TIMEOUT_SECONDS", 15))
    workers = max(1, min(4, env_int("ROAD_VECTOR_TILE_WORKERS", 2)))
    _road_progress(
        params,
        {
            "status": "running",
            "source": "osm_vector_tiles",
            "tiles_total": len(tiles),
            "zoom": zoom,
        },
    )

    def fetch(tile: tuple[int, int, Any]) -> tuple[int, int, Any, Path]:
        x, y, bounds = tile
        path = cache_root / str(zoom) / str(x) / f"{y}.pbf"
        if not path.exists() or path.stat().st_size == 0:
            url = spec["endpoint"].format(z=zoom, x=x, y=y)
            download_file(url, path, timeout=timeout, retries=1)
        return x, y, bounds, path

    fetched = []
    errors = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch, tile) for tile in tiles]
        for future in as_completed(futures):
            try:
                fetched.append(future.result())
            except (PermanentError, TransientError) as exc:
                errors.append(_compact_road_error(exc))
    if errors and not _vector_tile_batch_is_usable(len(fetched), len(tiles)):
        raise TransientError(
            f"OSM vector tile fallback failed for {len(errors)}/{len(tiles)} tiles: "
            + " | ".join(errors[:3])
        )
    partial_tiles = bool(errors)
    if partial_tiles:
        _road_progress(
            params,
            {
                "status": "degraded",
                "source": "osm_vector_tiles",
                "tiles_total": len(tiles),
                "tiles_downloaded": len(fetched),
                "tiles_failed": len(errors),
                "message": "Using bounded partial vector-tile coverage.",
            },
        )

    frames = []
    metadata = _source_columns("road_vector_tiles")
    for x, y, bounds, path in sorted(fetched):
        roads = None
        read_error: Exception | None = None
        for read_attempt in range(2):
            try:
                roads = gpd.read_file(path, layer="streets")
                break
            except Exception as exc:
                read_error = exc
                if read_attempt == 0:
                    path.unlink(missing_ok=True)
                    url = spec["endpoint"].format(z=zoom, x=x, y=y)
                    download_file(url, path, timeout=timeout, retries=2)
        if roads is None:
            raise PermanentError(
                f"Could not read OSM vector tile {zoom}/{x}/{y} after redownload: {read_error}"
            ) from read_error
        if roads.empty or "kind" not in roads.columns:
            continue
        if roads.crs is None:
            raise PermanentError(
                f"OSM vector tile {zoom}/{x}/{y} has no georeferencing."
            )
        if str(roads.crs) != "EPSG:3857":
            roads = roads.to_crs("EPSG:3857")
        base_classes = {value.removesuffix("_link") for value in road_classes}
        roads = roads[roads["kind"].isin(base_classes)].copy()
        if roads.empty:
            continue
        roads["geometry"] = roads.geometry.intersection(bounds)
        roads = roads[~roads.geometry.is_empty].copy()
        if roads.empty:
            continue
        link = roads.get("link", False)
        if not hasattr(link, "fillna"):
            link = pd.Series(False, index=roads.index)
        link = link.fillna(False).astype(bool)
        roads["highway"] = roads["kind"].astype(str)
        roads.loc[link, "highway"] = roads.loc[link, "highway"] + "_link"
        roads = roads[roads["highway"].isin(road_classes)].copy()
        if roads.empty:
            continue
        roads["osm_key"] = (
            f"mvt:{zoom}:{x}:{y}:" + roads["mvt_id"].astype(str)
        )
        roads["name"] = ""
        roads["road_level"] = "main"
        roads["tile_zoom"] = zoom
        roads["approximate_geometry"] = True
        for key, value in metadata.items():
            roads[key] = value
        frames.append(
            roads[[
                "osm_key", "name", "highway", "road_level", "dataset_id",
                "data_source", "data_license", "acquired_at", "tile_zoom",
                "approximate_geometry", "geometry",
            ]]
        )
    if not frames:
        raise PermanentError("OSM vector tiles contain no main roads in the analysis area.")
    merged = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:3857")
    analysis_geometry = area.to_crs("EPSG:3857").geometry.union_all()
    merged = merged[merged.geometry.intersects(analysis_geometry)].copy()
    if merged.empty:
        raise PermanentError("No vector-tile roads intersect the analysis geometry.")
    max_features = env_int("DATA_MAX_FEATURES", 200_000)
    if len(merged) > max_features:
        raise PermanentError(f"Road vector result exceeds feature limit: {len(merged)}")
    merged["tile_download_status"] = "partial" if partial_tiles else "complete"
    merged["tiles_requested"] = len(tiles)
    merged["tiles_failed"] = len(errors)
    merged = merged.to_crs("EPSG:4326")
    _write_layer(merged, output)
    _road_progress(
        params,
        {
            "status": "success",
            "source": "osm_vector_tiles",
            "tiles_total": len(tiles),
            "features": len(merged),
            "zoom": zoom,
        },
    )
    return {
        "output": str(output),
        "feature_count": int(len(merged)),
        "cache_hit": False,
        "road_level": "main",
        "road_source": "osm_shortbread_vector_tiles",
        "tile_count": len(tiles),
        "tile_count_downloaded": len(fetched),
        "tile_count_failed": len(errors),
        "partial_tiles": partial_tiles,
        "tile_zoom": zoom,
        "approximate_geometry": True,
    }


def download_osm_pois(params: dict[str, Any]) -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    gpd, _, Point, _ = _imports()
    boundary = gpd.read_file(params["BOUNDARY"])
    if boundary.empty or boundary.crs is None:
        raise PermanentError("Boundary layer is empty or has no CRS.")
    _validate_query_extent(boundary)
    poi_type = str(params.get("POI_TYPE", "university"))
    if poi_type not in POI_FILTERS:
        raise PermanentError(f"Unsupported POI_TYPE: {poi_type}")
    output = Path(params["OUTPUT"])
    spec = get_dataset_spec("osm_pois")
    bbox = _overpass_bbox(boundary)
    key = cache_key(
        "poi-v3",
        {
            "bbox": [round(v, 5) for v in bbox],
            "poi_type": poi_type,
            "query_version": 3,
        },
    )
    if restore_cached_layer(key, output):
        cached = gpd.read_file(output)
        return {
            "output": str(output),
            "feature_count": len(cached),
            "cache_hit": True,
            "data_source": "persistent_normalized_cache",
        }

    source_mode = env_str("POI_SOURCE_MODE", "auto").lower()
    if source_mode not in {"auto", "local_pbf", "overpass"}:
        raise PermanentError(f"Unsupported POI_SOURCE_MODE: {source_mode}")
    if source_mode in {"auto", "local_pbf"}:
        _poi_progress(
            params,
            {"status": "running", "source": "osm_local_pbf_snapshot", "poi_type": poi_type},
        )
        try:
            local_result = _download_pois_from_local_pbf(boundary, poi_type, output)
        except PermanentError:
            if source_mode == "local_pbf":
                raise
            local_result = None
        if local_result is not None:
            _poi_progress(
                params,
                {
                    "status": "success",
                    "source": "osm_local_pbf_snapshot",
                    "poi_type": poi_type,
                    "features": local_result["feature_count"],
                },
            )
            store_cached_layer(
                key,
                output,
                {"bbox": bbox, "poi_type": poi_type, "query_version": 3, **local_result},
            )
            return local_result
        if source_mode == "local_pbf":
            raise PermanentError("Configured local OSM snapshot is not available.")

    tile_degrees = env_float("POI_TILE_DEGREES", 0.5)
    tiles = _degree_tiles_for_area(boundary, tile_degrees)
    max_tiles = max(1, env_int("POI_MAX_TILES", 32))
    if not tiles:
        raise PermanentError("POI query contains no intersecting tiles.")
    if len(tiles) > max_tiles:
        raise BudgetExceededError(
            f"POI query needs {len(tiles)} tiles; configured limit is {max_tiles}."
        )
    workers = max(1, min(6, env_int("POI_TILE_WORKERS", 4)))
    request_timeout = max(5, env_int("POI_HTTP_TIMEOUT_SECONDS", 20))
    endpoint_attempts = max(1, min(3, env_int("POI_MAX_ENDPOINT_ATTEMPTS", 1)))
    query_timeout = max(5, env_int("POI_QUERY_TIMEOUT_SECONDS", 25))
    osm_types = ("node",) if poi_type == "subway_station" else (
        "node", "way", "relation"
    )
    _poi_progress(
        params,
        {
            "status": "running",
            "source": "overpass_tiled",
            "poi_type": poi_type,
            "tiles_total": len(tiles),
            "workers": workers,
        },
    )

    def fetch_tile(index: int, tile_bbox: tuple[float, float, float, float]):
        tile_key = cache_key(
            "poi-tile-v2",
            {
                "bbox": [round(value, 6) for value in tile_bbox],
                "poi_type": poi_type,
                "osm_types": list(osm_types),
                "query_version": 2,
            },
        )
        cached_response = restore_cached_json(tile_key)
        if isinstance(cached_response, dict) and isinstance(
            cached_response.get("elements"), list
        ):
            return index, cached_response, True
        response = _request_overpass(
            spec,
            _overpass_query(
                POI_FILTERS[poi_type],
                tile_bbox,
                "poi",
                osm_types=osm_types,
                query_timeout_seconds=query_timeout,
            ),
            start_index=index,
            timeout_seconds=request_timeout,
            max_endpoints=endpoint_attempts,
            rotate_endpoints=True,
        )
        store_cached_json(tile_key, response)
        return index, response, False

    responses: list[tuple[int, dict[str, Any], bool]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_tile, index, tile_bbox): index
            for index, tile_bbox in enumerate(tiles)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                result = future.result()
                responses.append(result)
                _poi_progress(
                    params,
                    {
                        "status": "success",
                        "source": "overpass_tiled",
                        "tile": index + 1,
                        "tiles_total": len(tiles),
                        "cache_hit": result[2],
                        "elements": len(result[1].get("elements", [])),
                    },
                )
            except (PermanentError, TransientError) as exc:
                concise = _compact_road_error(exc)
                errors.append(f"tile {index + 1}: {concise}")
                _poi_progress(
                    params,
                    {
                        "status": "failed",
                        "source": "overpass_tiled",
                        "tile": index + 1,
                        "tiles_total": len(tiles),
                        "error": concise,
                    },
                )

    if errors and not _poi_tile_batch_is_usable(len(responses), len(tiles)):
        raise TransientError(
            f"POI tiled download completed only {len(responses)}/{len(tiles)} tiles: "
            + " | ".join(errors[:3])
        )
    partial_tiles = bool(errors)
    if partial_tiles:
        _poi_progress(
            params,
            {
                "status": "degraded",
                "source": "overpass_tiled",
                "tiles_total": len(tiles),
                "tiles_downloaded": len(responses),
                "tiles_failed": len(errors),
                "message": "Using cached and live tiles above the completeness threshold.",
            },
        )

    elements_by_key: dict[str, dict[str, Any]] = {}
    for _, response, _ in responses:
        for element in response.get("elements", []):
            element_type = str(element.get("type") or "")
            element_id = element.get("id")
            if element_type and element_id is not None:
                elements_by_key[f"{element_type}:{element_id}"] = element
    rows = []
    source_metadata = _source_columns("osm_pois")
    for osm_key, element in elements_by_key.items():
        center = element.get("center", element)
        lon, lat = center.get("lon"), center.get("lat")
        if lon is None or lat is None:
            continue
        tags = element.get("tags", {})
        rows.append({
            "osm_key": osm_key,
            "name": tags.get("name") or tags.get("name:zh") or "unnamed",
            "amenity": tags.get("amenity", ""),
            "railway": tags.get("railway", ""),
            "shop": tags.get("shop", ""),
            "leisure": tags.get("leisure", ""),
            "poi_type": poi_type,
            "poi_label": POI_LABELS[poi_type],
            **source_metadata,
            "tile_download_status": "partial" if partial_tiles else "complete",
            "tiles_requested": len(tiles),
            "tiles_failed": len(errors),
            "geometry": Point(float(lon), float(lat)),
        })
    if not rows:
        raise PermanentError(f"Overpass returned no {POI_LABELS[poi_type]} POIs.")
    pois = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    boundary_4326 = boundary.to_crs("EPSG:4326")
    region_geometry = boundary_4326.geometry.union_all()
    pois = pois[pois.geometry.intersects(region_geometry)].copy()
    pois = pois.drop_duplicates(subset=["osm_key"])
    max_features = env_int("DATA_MAX_FEATURES", 200_000)
    if len(pois) > max_features:
        raise PermanentError(f"POI result exceeds feature limit: {len(pois)}")
    if pois.empty:
        raise PermanentError(f"No {POI_LABELS[poi_type]} POIs fall inside the boundary.")
    _write_layer(pois, output)
    if not partial_tiles:
        store_cached_layer(
            key,
            output,
            {"bbox": bbox, "poi_type": poi_type, "count": len(pois), "query_version": 2},
        )
    return {
        "output": str(output),
        "feature_count": len(pois),
        "cache_hit": False,
        "data_source": "overpass_tiled_with_persistent_tile_cache",
        "tile_count": len(tiles),
        "tile_count_downloaded": len(responses),
        "tile_count_failed": len(errors),
        "partial_tiles": partial_tiles,
    }


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
    road_level = str(params.get("ROAD_LEVEL", "main")).lower()
    if road_level not in {"all", "main"}:
        raise PermanentError(f"Unsupported ROAD_LEVEL: {road_level}")
    classes = MAIN_ROAD_CLASSES if road_level == "main" else ROAD_CLASSES
    if poi_type not in POI_FILTERS or distance_meters <= 0:
        raise PermanentError("Road download requires a supported POI type and positive distance.")
    source_mode = env_str("ROAD_SOURCE_MODE", "auto").lower()
    if source_mode not in {"auto", "geofabrik", "overpass", "vector_tiles"}:
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
    tile_degrees = (
        env_float("MAIN_ROAD_TILE_DEGREES", 0.2)
        if road_level == "main"
        else env_float("OVERPASS_TILE_DEGREES", 0.2)
    )
    batches = _road_query_tiles(
        point_coordinates,
        distance_meters,
        tile_degrees=tile_degrees,
    )
    if not batches:
        raise PermanentError("Road query contains no valid point coordinates.")
    if len(batches) > max_batches:
        raise PermanentError(
            f"Road query needs {len(batches)} batches; configured limit is {max_batches}."
        )
    key = cache_key(
        "roads-v2",
        {
            "bbox": [round(v, 5) for v in bbox], "poi_type": poi_type,
            "distance": distance_meters, "points": point_coordinates,
            "source_mode": source_mode, "road_level": road_level,
        },
    )
    if restore_cached_layer(key, output):
        cached = gpd.read_file(output)
        return {
            "output": str(output), "feature_count": len(cached),
            "cache_hit": True, "road_level": road_level,
        }

    if _should_try_geofabrik(source_mode):
        _road_progress(params, {"status": "running", "source": "geofabrik"})
        try:
            extract_result = _download_roads_from_extract(
                region_name,
                area,
                output,
                road_classes=classes,
            )
        except (PermanentError, TransientError):
            if source_mode == "geofabrik":
                raise
            extract_result = None
        if extract_result is not None:
            _road_progress(
                params,
                {
                    "status": "success",
                    "source": "geofabrik",
                    "features": extract_result.get("feature_count"),
                },
            )
            store_cached_layer(
                key, output,
                {"region_name": region_name, "distance": distance_meters, **extract_result},
            )
            return {**extract_result, "road_level": road_level}

    if source_mode in {"auto", "vector_tiles"} and road_level == "main":
        try:
            vector_result = _download_roads_from_vector_tiles(
                area,
                output,
                classes,
                params,
            )
        except (PermanentError, TransientError, BudgetExceededError):
            if source_mode == "vector_tiles":
                raise
        else:
            store_cached_layer(
                key,
                output,
                {
                    "region_name": region_name,
                    "distance": distance_meters,
                    **vector_result,
                },
            )
            return vector_result

    deadline_seconds, request_timeout, endpoint_attempts = _road_request_settings()
    road_started = time.monotonic()
    elements_by_id: dict[int, dict[str, Any]] = {}
    for batch_index, batch_bbox in enumerate(batches):
        batch_elements = _request_road_elements(
            params,
            spec,
            batch_bbox,
            _road_filter(classes),
            batch_label=batch_index + 1,
            total_batches=len(batches),
            start_index=batch_index,
            started_at=road_started,
            deadline_seconds=deadline_seconds,
            request_timeout=request_timeout,
            endpoint_attempts=endpoint_attempts,
        )
        for element in batch_elements:
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
        highway = tags.get("highway", "")
        if highway not in classes:
            continue
        rows.append({
            "osm_key": f"way:{element.get('id')}",
            "name": tags.get("name", ""),
            "highway": highway,
            "road_level": road_level,
            **_source_columns("road_network"),
            "geometry": LineString(line_coordinates),
        })
    if not rows:
        raise PermanentError(f"Overpass returned no {road_level} road features.")
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
            "point_count": len(point_coordinates), "batch_count": len(batches),
            "count": len(roads), "road_level": road_level,
        },
    )
    return {
        "output": str(output), "feature_count": len(roads), "cache_hit": False,
        "point_count": len(point_coordinates), "batch_count": len(batches),
        "road_level": road_level,
    }


def download_osm_roads_in_area(params: dict[str, Any]) -> dict[str, Any]:
    """Download main roads by default inside a boundary/analysis area."""
    gpd, LineString, _, _ = _imports()
    area = gpd.read_file(params["AREA"])
    if area.empty or area.crs is None:
        raise PermanentError("Road query area is empty or has no CRS.")
    _validate_query_extent(area)
    output = Path(params["OUTPUT"])
    region_name = str(params["REGION_NAME"]).strip()
    road_level = str(params.get("ROAD_LEVEL", "main"))
    if road_level not in {"all", "main"}:
        raise PermanentError(f"Unsupported ROAD_LEVEL: {road_level}")
    classes = ROAD_CLASSES if road_level == "all" else MAIN_ROAD_CLASSES
    source_mode = env_str("ROAD_SOURCE_MODE", "auto").lower()
    if source_mode not in {"auto", "geofabrik", "overpass", "vector_tiles"}:
        raise PermanentError(f"Unsupported ROAD_SOURCE_MODE: {source_mode}")
    spec = get_dataset_spec("road_network")
    bbox = _overpass_bbox(area)
    key = cache_key(
        "roads-in-area-v2",
        {
            "bbox": [round(v, 5) for v in bbox],
            "region_name": region_name,
            "road_level": road_level,
            "source_mode": source_mode,
        },
    )
    if restore_cached_layer(key, output):
        cached = gpd.read_file(output)
        return {
            "output": str(output), "feature_count": len(cached),
            "cache_hit": True, "road_level": road_level,
        }

    if _should_try_geofabrik(source_mode):
        _road_progress(params, {"status": "running", "source": "geofabrik"})
        try:
            extracted = _download_roads_from_extract(
                region_name,
                area,
                output,
                road_classes=classes,
            )
        except (PermanentError, TransientError):
            if source_mode == "geofabrik":
                raise
            extracted = None
        if extracted is not None:
            _road_progress(
                params,
                {
                    "status": "success",
                    "source": "geofabrik",
                    "features": extracted.get("feature_count"),
                },
            )
            store_cached_layer(key, output, {"road_level": road_level, **extracted})
            return {**extracted, "road_level": road_level}

    if source_mode in {"auto", "vector_tiles"} and road_level == "main":
        try:
            vector_result = _download_roads_from_vector_tiles(
                area,
                output,
                classes,
                params,
            )
        except (PermanentError, TransientError, BudgetExceededError):
            if source_mode == "vector_tiles":
                raise
        else:
            store_cached_layer(key, output, vector_result)
            return vector_result

    road_filter = _road_filter(classes)
    deadline_seconds, request_timeout, endpoint_attempts = _road_request_settings()
    road_started = time.monotonic()
    elements = _request_road_elements(
        params,
        spec,
        bbox,
        road_filter,
        batch_label=1,
        total_batches=1,
        start_index=0,
        started_at=road_started,
        deadline_seconds=deadline_seconds,
        request_timeout=request_timeout,
        endpoint_attempts=endpoint_attempts,
    )
    rows = []
    for element in elements:
        coordinates = [
            (float(point["lon"]), float(point["lat"]))
            for point in element.get("geometry", [])
            if "lon" in point and "lat" in point
        ]
        if element.get("type") != "way" or len(coordinates) < 2:
            continue
        tags = element.get("tags", {})
        highway = tags.get("highway", "")
        if highway not in classes:
            continue
        rows.append(
            {
                "osm_key": f"way:{element.get('id')}",
                "name": tags.get("name", ""),
                "highway": highway,
                "road_level": road_level,
                **_source_columns("road_network"),
                "geometry": LineString(coordinates),
            }
        )
    if not rows:
        raise PermanentError(f"Overpass returned no {road_level} road features.")
    roads = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    roads = roads.drop_duplicates(subset=["osm_key"])
    region = area.to_crs("EPSG:4326").geometry.union_all()
    roads = roads[roads.geometry.intersects(region)].copy()
    if roads.empty:
        raise PermanentError("No roads intersect the requested analysis area.")
    _write_layer(roads, output)
    store_cached_layer(
        key,
        output,
        {"bbox": bbox, "road_level": road_level, "count": len(roads)},
    )
    return {
        "output": str(output),
        "feature_count": int(len(roads)),
        "cache_hit": False,
        "road_level": road_level,
    }


def _download_water_from_vector_tiles(
    boundary,
    output: Path,
    params: dict[str, Any],
) -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    import geopandas as gpd
    import pandas as pd

    zoom = max(5, min(14, env_int("ROAD_VECTOR_TILE_ZOOM", 11)))
    tiles = _vector_tiles_for_area(boundary, zoom)
    max_tiles = max(1, env_int("ROAD_VECTOR_TILE_MAX_TILES", 120))
    if not tiles:
        raise PermanentError("No OSM vector tiles intersect the water analysis area.")
    if len(tiles) > max_tiles:
        raise BudgetExceededError(
            f"Water vector source needs {len(tiles)} tiles; configured limit is {max_tiles}."
        )
    spec = get_dataset_spec("water_vector_tiles")
    cache_root = PROJECT_ROOT / "outputs" / "data_cache" / "vector_tiles" / "shortbread_v1"
    timeout = max(3, env_int("ROAD_VECTOR_TILE_TIMEOUT_SECONDS", 15))
    workers = max(1, min(4, env_int("ROAD_VECTOR_TILE_WORKERS", 2)))
    _water_progress(
        params,
        {
            "status": "running",
            "source": "osm_vector_tiles",
            "tiles_total": len(tiles),
            "zoom": zoom,
        },
    )

    def fetch(tile: tuple[int, int, Any]) -> tuple[int, int, Any, Path]:
        x, y, bounds = tile
        path = cache_root / str(zoom) / str(x) / f"{y}.pbf"
        if not path.exists() or path.stat().st_size == 0:
            url = spec["endpoint"].format(z=zoom, x=x, y=y)
            download_file(url, path, timeout=timeout, retries=1)
        return x, y, bounds, path

    fetched: list[tuple[int, int, Any, Path]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch, tile) for tile in tiles]
        for future in as_completed(futures):
            try:
                fetched.append(future.result())
            except (PermanentError, TransientError) as exc:
                errors.append(_compact_road_error(exc))
    if errors and not _vector_tile_batch_is_usable(len(fetched), len(tiles)):
        raise TransientError(
            f"Water vector download failed for {len(errors)}/{len(tiles)} tiles: "
            + " | ".join(errors[:3])
        )

    frames = []
    read_failures = 0
    metadata = _source_columns("water_vector_tiles")
    for x, y, bounds, path in sorted(fetched):
        try:
            water = gpd.read_file(path, layer="water_polygons")
        except Exception as exc:
            errors.append(f"tile {zoom}/{x}/{y}: {_compact_road_error(exc)}")
            read_failures += 1
            continue
        if water.empty:
            continue
        if water.crs is None:
            errors.append(f"tile {zoom}/{x}/{y}: missing CRS")
            read_failures += 1
            continue
        if str(water.crs) != "EPSG:3857":
            water = water.to_crs("EPSG:3857")
        water["geometry"] = water.geometry.intersection(bounds)
        water = water[~water.geometry.is_empty].copy().reset_index(drop=True)
        if water.empty:
            continue
        water["osm_key"] = [
            f"mvt-water:{zoom}:{x}:{y}:{index}" for index in range(len(water))
        ]
        water["name"] = ""
        water["water_type"] = water.get("kind", "water")
        for column, value in metadata.items():
            water[column] = value
        frames.append(
            water[[
                "osm_key", "name", "water_type", "dataset_id", "data_source",
                "data_license", "acquired_at", "geometry",
            ]]
        )
    successful_tile_reads = len(fetched) - read_failures
    if not _vector_tile_batch_is_usable(successful_tile_reads, len(tiles)):
        raise TransientError(
            f"Water vector source produced only {successful_tile_reads}/{len(tiles)} "
            "readable tiles."
        )
    partial_tiles = bool(errors)
    if not frames:
        raise PermanentError("OSM vector tiles contain no water polygons in the area.")

    waters = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:3857")
    region = boundary.to_crs("EPSG:3857").geometry.union_all()
    waters = waters[waters.geometry.intersects(region)].copy()
    waters["geometry"] = waters.geometry.intersection(region)
    waters = waters[~waters.geometry.is_empty].copy()
    if waters.empty:
        raise PermanentError("No vector-tile water polygons intersect the boundary.")
    waters["tile_download_status"] = "partial" if partial_tiles else "complete"
    waters["tiles_requested"] = len(tiles)
    waters["tiles_failed"] = len(errors)
    waters = waters.to_crs("EPSG:4326")
    _write_layer(waters, output)
    _water_progress(
        params,
        {
            "status": "success" if not partial_tiles else "degraded",
            "source": "osm_vector_tiles",
            "tiles_total": len(tiles),
            "tiles_failed": len(errors),
            "features": len(waters),
        },
    )
    return {
        "output": str(output),
        "feature_count": int(len(waters)),
        "cache_hit": False,
        "data_source": "osm_shortbread_vector_tiles",
        "tile_count": len(tiles),
        "tile_count_failed": len(errors),
        "partial_tiles": partial_tiles,
    }


def _overpass_water_query(bbox: tuple[float, float, float, float]) -> str:
    timeout = env_int("OVERPASS_QUERY_TIMEOUT_SECONDS", 120)
    south, west, north, east = bbox
    bbox_text = f"{south:.7f},{west:.7f},{north:.7f},{east:.7f}"
    filters = (
        '["natural"="water"]',
        '["waterway"="riverbank"]',
        '["landuse"~"^(reservoir|basin)$"]',
    )
    selectors = "\n".join(f"  way{item}({bbox_text});" for item in filters)
    return f"[out:json][timeout:{timeout}];\n(\n{selectors}\n);\nout geom tags qt;"


def download_osm_water(params: dict[str, Any]) -> dict[str, Any]:
    """Download closed OSM water polygons for exclusion and distance analysis."""
    import geopandas as gpd
    from shapely.geometry import Polygon

    boundary = gpd.read_file(params["BOUNDARY"])
    if boundary.empty or boundary.crs is None:
        raise PermanentError("Boundary layer is empty or has no CRS.")
    _validate_query_extent(boundary)
    output = Path(params["OUTPUT"])
    bbox = _overpass_bbox(boundary)
    key = cache_key("water-v2", {"bbox": [round(v, 5) for v in bbox]})
    if restore_cached_layer(key, output):
        cached = gpd.read_file(output)
        return {"output": str(output), "feature_count": len(cached), "cache_hit": True}

    source_mode = env_str("WATER_SOURCE_MODE", env_str("ROAD_SOURCE_MODE", "auto")).lower()
    if source_mode not in {"auto", "local_pbf", "vector_tiles", "overpass"}:
        raise PermanentError(f"Unsupported WATER_SOURCE_MODE: {source_mode}")
    if source_mode in {"auto", "local_pbf"}:
        _water_progress(params, {"status": "running", "source": "osm_local_pbf_snapshot"})
        try:
            local_result = _download_water_from_local_pbf(boundary, output)
        except PermanentError:
            if source_mode == "local_pbf":
                raise
            local_result = None
        if local_result is not None:
            _water_progress(
                params,
                {
                    "status": "success",
                    "source": "osm_local_pbf_snapshot",
                    "features": local_result["feature_count"],
                },
            )
            store_cached_layer(key, output, {"bbox": bbox, **local_result})
            return local_result
        if source_mode == "local_pbf":
            raise PermanentError("Configured local OSM snapshot is not available.")
    if source_mode in {"auto", "vector_tiles"}:
        try:
            vector_result = _download_water_from_vector_tiles(boundary, output, params)
        except (PermanentError, TransientError, BudgetExceededError):
            if source_mode == "vector_tiles":
                raise
            vector_result = None
        if vector_result is not None:
            if not vector_result.get("partial_tiles"):
                store_cached_layer(key, output, {"bbox": bbox, **vector_result})
            return vector_result

    spec = get_dataset_spec("water_areas")
    response = _request_overpass(spec, _overpass_water_query(bbox))
    rows = []
    for element in response.get("elements", []):
        coordinates = [
            (float(point["lon"]), float(point["lat"]))
            for point in element.get("geometry", [])
            if "lon" in point and "lat" in point
        ]
        if element.get("type") != "way" or len(coordinates) < 4:
            continue
        if coordinates[0] != coordinates[-1]:
            coordinates.append(coordinates[0])
        geometry = Polygon(coordinates)
        if not geometry.is_valid:
            geometry = geometry.buffer(0)
        if geometry.is_empty:
            continue
        tags = element.get("tags", {})
        rows.append(
            {
                "osm_key": f"way:{element.get('id')}",
                "name": tags.get("name", ""),
                "water_type": tags.get("water") or tags.get("natural") or tags.get("landuse", ""),
                **_source_columns("water_areas"),
                "geometry": geometry,
            }
        )
    if not rows:
        raise PermanentError("Overpass returned no closed water polygons.")
    waters = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    region = boundary.to_crs("EPSG:4326").geometry.union_all()
    waters = waters[waters.geometry.intersects(region)].copy()
    waters["geometry"] = waters.geometry.intersection(region)
    waters = waters[~waters.geometry.is_empty].drop_duplicates(subset=["osm_key"])
    if waters.empty:
        raise PermanentError("No water polygons intersect the requested boundary.")
    _write_layer(waters, output)
    store_cached_layer(key, output, {"bbox": bbox, "count": len(waters)})
    return {"output": str(output), "feature_count": int(len(waters)), "cache_hit": False}


DATA_ACQUISITION_HANDLERS = {
    "download_region_boundary": download_region_boundary,
    "download_osm_pois": download_osm_pois,
    "download_osm_roads": download_osm_roads,
    "download_osm_roads_in_area": download_osm_roads_in_area,
    "download_osm_water": download_osm_water,
}
