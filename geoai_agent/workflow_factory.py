from __future__ import annotations

from typing import Any

from .dataset_catalog import SUPPORTED_POI_TYPES


def _step(tool: str, **params: Any) -> dict[str, Any]:
    return {"tool": tool, "params": params}


def _boundary_steps(region_name: str) -> list[dict[str, Any]]:
    return [
        _step(
            "download_region_boundary",
            REGION_NAME=region_name,
            OUTPUT="workspace://raw/region_boundary.gpkg",
        ),
        _step(
            "validate_dataset",
            INPUT="workspace://raw/region_boundary.gpkg",
            GEOMETRY_TYPE="polygon",
        ),
    ]


def _poi_steps(poi_type: str) -> list[dict[str, Any]]:
    if poi_type not in SUPPORTED_POI_TYPES:
        raise ValueError(f"Unsupported poi_type: {poi_type}")
    path = f"workspace://raw/{poi_type}_pois.gpkg"
    return [
        _step(
            "download_osm_pois",
            BOUNDARY="workspace://raw/region_boundary.gpkg",
            POI_TYPE=poi_type,
            OUTPUT=path,
        ),
        _step("validate_dataset", INPUT=path, GEOMETRY_TYPE="point"),
    ]


def _projected_boundary_step() -> dict[str, Any]:
    return _step(
        "auto_reproject_layer",
        INPUT="workspace://raw/region_boundary.gpkg",
        OUTPUT="workspace://processed/region_projected.gpkg",
    )


def _project_poi_step(poi_type: str) -> dict[str, Any]:
    return _step(
        "reproject_to_match",
        INPUT=f"workspace://raw/{poi_type}_pois.gpkg",
        REFERENCE="workspace://processed/region_projected.gpkg",
        OUTPUT=f"workspace://processed/{poi_type}_pois_projected.gpkg",
    )


def build_dynamic_plan(
    task_type: str,
    region_name: str,
    *,
    distance_meters: int = 0,
    poi_type: str = "university",
) -> dict[str, Any]:
    boundary = _boundary_steps(region_name)
    poi = _poi_steps(poi_type) if task_type not in {
        "administrative_area",
        "adjacent_regions",
        "road_density",
        "advanced_site_selection",
    } else []

    if task_type == "road_length_around_poi":
        distance_meters = distance_meters or 1000
        poi_raw = f"workspace://raw/{poi_type}_pois.gpkg"
        poi_projected = f"workspace://processed/{poi_type}_pois_projected.gpkg"
        workflow = {
            "workflow": "dynamic_road_length_around_poi",
            "steps": [
                *boundary,
                *poi,
                _step("auto_reproject_layer", INPUT=poi_raw, OUTPUT=poi_projected),
                _step(
                    "buffer",
                    INPUT=poi_projected,
                    DISTANCE=distance_meters,
                    SEGMENTS=12,
                    DISSOLVE=True,
                    OUTPUT="workspace://processed/poi_buffers.gpkg",
                ),
                _step(
                    "download_osm_roads",
                    AREA="workspace://processed/poi_buffers.gpkg",
                    POINTS=poi_raw,
                    REGION_NAME=region_name,
                    POI_TYPE=poi_type,
                    DISTANCE=distance_meters,
                    ROAD_LEVEL="main",
                    OUTPUT="workspace://raw/roads.gpkg",
                ),
                _step(
                    "validate_dataset",
                    INPUT="workspace://raw/roads.gpkg",
                    GEOMETRY_TYPE="line",
                ),
                _step(
                    "reproject_to_match",
                    INPUT="workspace://raw/roads.gpkg",
                    REFERENCE="workspace://processed/poi_buffers.gpkg",
                    OUTPUT="workspace://processed/roads_projected.gpkg",
                ),
                _step(
                    "clip",
                    INPUT="workspace://processed/roads_projected.gpkg",
                    OVERLAY="workspace://processed/poi_buffers.gpkg",
                    OUTPUT="workspace://processed/roads_clip.gpkg",
                ),
                _step(
                    "sum_line_lengths",
                    POLYGONS="workspace://processed/poi_buffers.gpkg",
                    LINES="workspace://processed/roads_clip.gpkg",
                    LEN_FIELD="road_length",
                    COUNT_FIELD="road_count",
                    OUTPUT="workspace://result/road_length_around_pois.gpkg",
                ),
            ],
        }
        requirements = ["administrative_boundary", "osm_pois", "road_network"]
    elif task_type == "administrative_area":
        workflow = {
            "workflow": "dynamic_administrative_area",
            "steps": [
                *boundary,
                _projected_boundary_step(),
                _step(
                    "calculate_polygon_area",
                    INPUT="workspace://processed/region_projected.gpkg",
                    AREA_FIELD="area_sq_km",
                    OUTPUT="workspace://result/region_area.gpkg",
                ),
            ],
        }
        requirements = ["administrative_boundary"]
        poi_type = ""
        distance_meters = 0
    elif task_type in {"university_count", "poi_count"}:
        workflow = {
            "workflow": (
                "dynamic_university_count"
                if task_type == "university_count"
                else "dynamic_poi_count"
            ),
            "steps": [
                *boundary,
                *poi,
                _step(
                    "count_points_in_polygon",
                    POLYGONS="workspace://raw/region_boundary.gpkg",
                    POINTS=f"workspace://raw/{poi_type}_pois.gpkg",
                    COUNT_FIELD="point_count",
                    OUTPUT=f"workspace://result/{poi_type}_count.gpkg",
                ),
            ],
        }
        requirements = [
            "administrative_boundary",
            "university_pois" if task_type == "university_count" else "osm_pois",
        ]
        distance_meters = 0
    elif task_type == "poi_service_area":
        distance_meters = distance_meters or 1000
        workflow = {
            "workflow": "dynamic_poi_service_area",
            "steps": [
                *boundary,
                *poi,
                _projected_boundary_step(),
                _project_poi_step(poi_type),
                _step(
                    "buffer",
                    INPUT=f"workspace://processed/{poi_type}_pois_projected.gpkg",
                    DISTANCE=distance_meters,
                    SEGMENTS=12,
                    DISSOLVE=True,
                    OUTPUT="workspace://processed/service_buffers.gpkg",
                ),
                _step(
                    "clip",
                    INPUT="workspace://processed/service_buffers.gpkg",
                    OVERLAY="workspace://processed/region_projected.gpkg",
                    OUTPUT="workspace://processed/service_area_clipped.gpkg",
                ),
                _step(
                    "calculate_polygon_area",
                    INPUT="workspace://processed/service_area_clipped.gpkg",
                    AREA_FIELD="coverage_sq_km",
                    OUTPUT="workspace://result/poi_service_area.gpkg",
                ),
            ],
        }
        requirements = ["administrative_boundary", "osm_pois"]
    elif task_type == "poi_density":
        workflow = {
            "workflow": "dynamic_poi_density",
            "steps": [
                *boundary,
                *poi,
                _projected_boundary_step(),
                _project_poi_step(poi_type),
                _step(
                    "point_density_grid",
                    BOUNDARY="workspace://processed/region_projected.gpkg",
                    POINTS=f"workspace://processed/{poi_type}_pois_projected.gpkg",
                    CELL_SIZE=5000,
                    OUTPUT="workspace://result/poi_density_grid.gpkg",
                ),
            ],
        }
        requirements = ["administrative_boundary", "osm_pois"]
        distance_meters = 0
    elif task_type == "poi_nearest_neighbor":
        workflow = {
            "workflow": "dynamic_poi_nearest_neighbor",
            "steps": [
                *boundary,
                *poi,
                _projected_boundary_step(),
                _project_poi_step(poi_type),
                _step(
                    "nearest_neighbor_analysis",
                    INPUT=f"workspace://processed/{poi_type}_pois_projected.gpkg",
                    DISTANCE_FIELD="nearest_neighbor_m",
                    OUTPUT="workspace://result/poi_nearest_neighbor.gpkg",
                ),
            ],
        }
        requirements = ["administrative_boundary", "osm_pois"]
        distance_meters = 0
    elif task_type == "service_gap_analysis":
        distance_meters = distance_meters or 1000
        workflow = {
            "workflow": "dynamic_service_gap_analysis",
            "steps": [
                *boundary,
                *poi,
                _projected_boundary_step(),
                _project_poi_step(poi_type),
                _step(
                    "buffer",
                    INPUT=f"workspace://processed/{poi_type}_pois_projected.gpkg",
                    DISTANCE=distance_meters,
                    SEGMENTS=12,
                    DISSOLVE=True,
                    OUTPUT="workspace://processed/service_buffers.gpkg",
                ),
                _step(
                    "service_gap_analysis",
                    BOUNDARY="workspace://processed/region_projected.gpkg",
                    COVERAGE="workspace://processed/service_buffers.gpkg",
                    DISTANCE=distance_meters,
                    OUTPUT="workspace://result/service_gap.gpkg",
                ),
            ],
        }
        requirements = ["administrative_boundary", "osm_pois"]
    elif task_type == "multi_ring_service_analysis":
        workflow = {
            "workflow": "dynamic_multi_ring_service_analysis",
            "steps": [
                *boundary,
                *poi,
                _projected_boundary_step(),
                _project_poi_step(poi_type),
                _step(
                    "multi_ring_service_analysis",
                    BOUNDARY="workspace://processed/region_projected.gpkg",
                    POINTS=f"workspace://processed/{poi_type}_pois_projected.gpkg",
                    DISTANCES="500,1000,2000",
                    OUTPUT="workspace://result/multi_ring_service.gpkg",
                ),
            ],
        }
        requirements = ["administrative_boundary", "osm_pois"]
        distance_meters = 0
    elif task_type == "poi_road_accessibility":
        workflow = {
            "workflow": "dynamic_poi_road_accessibility",
            "steps": [
                *boundary,
                *poi,
                _projected_boundary_step(),
                _project_poi_step(poi_type),
                _step(
                    "download_osm_roads_in_area",
                    AREA="workspace://raw/region_boundary.gpkg",
                    REGION_NAME=region_name,
                    ROAD_LEVEL="main",
                    OUTPUT="workspace://raw/roads.gpkg",
                ),
                _step(
                    "validate_dataset",
                    INPUT="workspace://raw/roads.gpkg",
                    GEOMETRY_TYPE="line",
                ),
                _step(
                    "reproject_to_match",
                    INPUT="workspace://raw/roads.gpkg",
                    REFERENCE="workspace://processed/region_projected.gpkg",
                    OUTPUT="workspace://processed/roads_projected.gpkg",
                ),
                _step(
                    "nearest_distance_to_features",
                    INPUT=f"workspace://processed/{poi_type}_pois_projected.gpkg",
                    TARGET="workspace://processed/roads_projected.gpkg",
                    DISTANCE_FIELD="nearest_road_m",
                    OUTPUT="workspace://result/poi_road_accessibility.gpkg",
                ),
            ],
        }
        requirements = ["administrative_boundary", "osm_pois", "road_network"]
        distance_meters = 0
    elif task_type == "road_density":
        workflow = {
            "workflow": "dynamic_road_density",
            "steps": [
                *boundary,
                _projected_boundary_step(),
                _step(
                    "download_osm_roads_in_area",
                    AREA="workspace://raw/region_boundary.gpkg",
                    REGION_NAME=region_name,
                    ROAD_LEVEL="main",
                    OUTPUT="workspace://raw/roads.gpkg",
                ),
                _step(
                    "validate_dataset",
                    INPUT="workspace://raw/roads.gpkg",
                    GEOMETRY_TYPE="line",
                ),
                _step(
                    "reproject_to_match",
                    INPUT="workspace://raw/roads.gpkg",
                    REFERENCE="workspace://processed/region_projected.gpkg",
                    OUTPUT="workspace://processed/roads_projected.gpkg",
                ),
                _step(
                    "line_density_grid",
                    BOUNDARY="workspace://processed/region_projected.gpkg",
                    LINES="workspace://processed/roads_projected.gpkg",
                    CELL_SIZE=5000,
                    OUTPUT="workspace://result/road_density_grid.gpkg",
                ),
            ],
        }
        requirements = ["administrative_boundary", "road_network"]
        poi_type = ""
        distance_meters = 0
    elif task_type == "adjacent_regions":
        workflow = {
            "workflow": "fixture_adjacent_regions",
            "steps": [
                _step(
                    "load_neighbor_boundaries",
                    REGION_NAME=region_name,
                    OUTPUT="workspace://raw/neighbor_boundaries.gpkg",
                ),
                _step(
                    "validate_dataset",
                    INPUT="workspace://raw/neighbor_boundaries.gpkg",
                    GEOMETRY_TYPE="polygon",
                ),
                _step(
                    "select_feature_by_attribute",
                    INPUT="workspace://raw/neighbor_boundaries.gpkg",
                    FIELD="region_name",
                    VALUE=region_name,
                    OUTPUT="workspace://processed/target_region.gpkg",
                ),
                _step(
                    "find_adjacent_polygons",
                    INPUT="workspace://raw/neighbor_boundaries.gpkg",
                    TARGET="workspace://processed/target_region.gpkg",
                    NAME_FIELD="region_name",
                    TOLERANCE_M=100,
                    OUTPUT="workspace://result/adjacent_regions.gpkg",
                ),
            ],
        }
        requirements = ["neighbor_boundaries"]
        poi_type = ""
        distance_meters = 0
    elif task_type == "multi_criteria_site_selection":
        road_distance = distance_meters or 3000
        workflow = {
            "workflow": "dynamic_multi_criteria_site_selection",
            "steps": [
                *boundary,
                *_poi_steps("university"),
                _projected_boundary_step(),
                _project_poi_step("university"),
                _step(
                    "download_osm_roads",
                    AREA="workspace://raw/region_boundary.gpkg",
                    POINTS="workspace://raw/university_pois.gpkg",
                    REGION_NAME=region_name,
                    POI_TYPE="university",
                    DISTANCE=road_distance,
                    ROAD_LEVEL="main",
                    OUTPUT="workspace://raw/roads.gpkg",
                ),
                _step(
                    "validate_dataset",
                    INPUT="workspace://raw/roads.gpkg",
                    GEOMETRY_TYPE="line",
                ),
                _step(
                    "reproject_to_match",
                    INPUT="workspace://raw/roads.gpkg",
                    REFERENCE="workspace://processed/region_projected.gpkg",
                    OUTPUT="workspace://processed/roads_projected.gpkg",
                ),
                _step(
                    "multi_criteria_site_selection",
                    BOUNDARY="workspace://processed/region_projected.gpkg",
                    FACILITIES="workspace://processed/university_pois_projected.gpkg",
                    ROADS="workspace://processed/roads_projected.gpkg",
                    CELL_SIZE=4000,
                    TOP_N=10,
                    ROAD_WEIGHT=0.45,
                    FACILITY_WEIGHT=0.35,
                    INTERIOR_WEIGHT=0.20,
                    MAX_ROAD_DISTANCE=road_distance,
                    MAX_FACILITY_DISTANCE=5000,
                    OUTPUT="workspace://result/site_selection_candidates.gpkg",
                ),
            ],
        }
        requirements = ["administrative_boundary", "university_pois", "road_network"]
        poi_type = "university"
        distance_meters = road_distance
    elif task_type == "advanced_site_selection":
        road_distance = distance_meters or 1000
        workflow = {
            "workflow": "dynamic_advanced_site_selection",
            "steps": [
                *boundary,
                *_poi_steps("university"),
                *_poi_steps("subway_station"),
                _step(
                    "download_osm_roads_in_area",
                    AREA="workspace://raw/region_boundary.gpkg",
                    REGION_NAME=region_name,
                    ROAD_LEVEL="main",
                    OUTPUT="workspace://raw/main_roads.gpkg",
                ),
                _step(
                    "validate_dataset",
                    INPUT="workspace://raw/main_roads.gpkg",
                    GEOMETRY_TYPE="line",
                ),
                _step(
                    "download_osm_water",
                    BOUNDARY="workspace://raw/region_boundary.gpkg",
                    OUTPUT="workspace://raw/water_areas.gpkg",
                ),
                _step(
                    "validate_dataset",
                    INPUT="workspace://raw/water_areas.gpkg",
                    GEOMETRY_TYPE="polygon",
                ),
                _projected_boundary_step(),
                _project_poi_step("university"),
                _project_poi_step("subway_station"),
                _step(
                    "reproject_to_match",
                    INPUT="workspace://raw/main_roads.gpkg",
                    REFERENCE="workspace://processed/region_projected.gpkg",
                    OUTPUT="workspace://processed/main_roads_projected.gpkg",
                ),
                _step(
                    "reproject_to_match",
                    INPUT="workspace://raw/water_areas.gpkg",
                    REFERENCE="workspace://processed/region_projected.gpkg",
                    OUTPUT="workspace://processed/water_projected.gpkg",
                ),
                _step(
                    "advanced_site_selection",
                    BOUNDARY="workspace://processed/region_projected.gpkg",
                    FACILITIES="workspace://processed/university_pois_projected.gpkg",
                    TRANSIT="workspace://processed/subway_station_pois_projected.gpkg",
                    ROADS="workspace://processed/main_roads_projected.gpkg",
                    WATER="workspace://processed/water_projected.gpkg",
                    CELL_SIZE=2000,
                    TOP_N=15,
                    ROAD_WEIGHT=0.35,
                    TRANSIT_WEIGHT=0.30,
                    FACILITY_WEIGHT=0.20,
                    INTERIOR_WEIGHT=0.15,
                    MAX_ROAD_DISTANCE=road_distance,
                    MAX_TRANSIT_DISTANCE=3000,
                    MAX_FACILITY_DISTANCE=5000,
                    MIN_WATER_DISTANCE=500,
                    OUTPUT="workspace://result/advanced_site_candidates.gpkg",
                ),
            ],
        }
        requirements = [
            "administrative_boundary",
            "university_pois",
            "subway_station_pois",
            "main_road_network",
            "water_areas",
        ]
        poi_type = "university"
        distance_meters = road_distance
    else:
        raise ValueError(f"Unsupported task_type: {task_type}")

    return {
        "supported": True,
        "task_type": task_type,
        "region_name": region_name,
        "poi_type": poi_type,
        "distance_meters": distance_meters,
        "data_requirements": requirements,
        "workflow": workflow,
        "reason": "",
    }
