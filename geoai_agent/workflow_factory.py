from __future__ import annotations

from typing import Any


def build_dynamic_plan(
    task_type: str,
    region_name: str,
    *,
    distance_meters: int = 0,
) -> dict[str, Any]:
    boundary_download = {
        "tool": "download_region_boundary",
        "params": {"REGION_NAME": region_name, "OUTPUT": "workspace://raw/region_boundary.gpkg"},
    }
    boundary_validation = {
        "tool": "validate_dataset",
        "params": {"INPUT": "workspace://raw/region_boundary.gpkg", "GEOMETRY_TYPE": "polygon"},
    }
    poi_download = {
        "tool": "download_osm_pois",
        "params": {
            "BOUNDARY": "workspace://raw/region_boundary.gpkg",
            "POI_TYPE": "university",
            "OUTPUT": "workspace://raw/university_pois.gpkg",
        },
    }
    poi_validation = {
        "tool": "validate_dataset",
        "params": {"INPUT": "workspace://raw/university_pois.gpkg", "GEOMETRY_TYPE": "point"},
    }
    if task_type == "road_length_around_poi":
        if distance_meters <= 0:
            raise ValueError("distance_meters must be positive.")
        workflow = {
            "workflow": "dynamic_road_length_around_poi",
            "steps": [
                boundary_download,
                boundary_validation,
                poi_download,
                poi_validation,
                {"tool": "auto_reproject_layer", "params": {"INPUT": "workspace://raw/university_pois.gpkg", "OUTPUT": "workspace://processed/university_pois_projected.gpkg"}},
                {"tool": "buffer", "params": {"INPUT": "workspace://processed/university_pois_projected.gpkg", "DISTANCE": distance_meters, "SEGMENTS": 12, "DISSOLVE": True, "OUTPUT": "workspace://processed/university_buffers.gpkg"}},
                {"tool": "download_osm_roads", "params": {"AREA": "workspace://processed/university_buffers.gpkg", "POINTS": "workspace://raw/university_pois.gpkg", "REGION_NAME": region_name, "POI_TYPE": "university", "DISTANCE": distance_meters, "OUTPUT": "workspace://raw/roads.gpkg"}},
                {"tool": "validate_dataset", "params": {"INPUT": "workspace://raw/roads.gpkg", "GEOMETRY_TYPE": "line"}},
                {"tool": "reproject_to_match", "params": {"INPUT": "workspace://raw/roads.gpkg", "REFERENCE": "workspace://processed/university_buffers.gpkg", "OUTPUT": "workspace://processed/roads_projected.gpkg"}},
                {"tool": "clip", "params": {"INPUT": "workspace://processed/roads_projected.gpkg", "OVERLAY": "workspace://processed/university_buffers.gpkg", "OUTPUT": "workspace://processed/roads_clip.gpkg"}},
                {"tool": "sum_line_lengths", "params": {"POLYGONS": "workspace://processed/university_buffers.gpkg", "LINES": "workspace://processed/roads_clip.gpkg", "LEN_FIELD": "road_length", "COUNT_FIELD": "road_count", "OUTPUT": "workspace://result/road_length_around_universities.gpkg"}},
            ],
        }
        requirements = ["administrative_boundary", "university_pois", "road_network"]
        poi_type = "university"
    elif task_type == "administrative_area":
        workflow = {
            "workflow": "dynamic_administrative_area",
            "steps": [
                boundary_download,
                boundary_validation,
                {"tool": "auto_reproject_layer", "params": {"INPUT": "workspace://raw/region_boundary.gpkg", "OUTPUT": "workspace://processed/region_projected.gpkg"}},
                {"tool": "calculate_polygon_area", "params": {"INPUT": "workspace://processed/region_projected.gpkg", "AREA_FIELD": "area_sq_km", "OUTPUT": "workspace://result/region_area.gpkg"}},
            ],
        }
        requirements = ["administrative_boundary"]
        poi_type = ""
        distance_meters = 0
    elif task_type == "university_count":
        workflow = {
            "workflow": "dynamic_university_count",
            "steps": [
                boundary_download,
                boundary_validation,
                poi_download,
                poi_validation,
                {"tool": "count_points_in_polygon", "params": {"POLYGONS": "workspace://raw/region_boundary.gpkg", "POINTS": "workspace://raw/university_pois.gpkg", "COUNT_FIELD": "point_count", "OUTPUT": "workspace://result/university_count.gpkg"}},
            ],
        }
        requirements = ["administrative_boundary", "university_pois"]
        poi_type = "university"
        distance_meters = 0
    elif task_type == "adjacent_regions":
        workflow = {
            "workflow": "fixture_adjacent_regions",
            "steps": [
                {"tool": "load_neighbor_boundaries", "params": {"REGION_NAME": region_name, "OUTPUT": "workspace://raw/neighbor_boundaries.gpkg"}},
                {"tool": "validate_dataset", "params": {"INPUT": "workspace://raw/neighbor_boundaries.gpkg", "GEOMETRY_TYPE": "polygon"}},
                {"tool": "select_feature_by_attribute", "params": {"INPUT": "workspace://raw/neighbor_boundaries.gpkg", "FIELD": "region_name", "VALUE": region_name, "OUTPUT": "workspace://processed/target_region.gpkg"}},
                {"tool": "find_adjacent_polygons", "params": {"INPUT": "workspace://raw/neighbor_boundaries.gpkg", "TARGET": "workspace://processed/target_region.gpkg", "NAME_FIELD": "region_name", "TOLERANCE_M": 100, "OUTPUT": "workspace://result/adjacent_regions.gpkg"}},
            ],
        }
        requirements = ["neighbor_boundaries"]
        poi_type = ""
        distance_meters = 0
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
