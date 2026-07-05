from __future__ import annotations

import json
from typing import Any

from .config import env_bool, env_str
from .llm_client import (
    LLMClientError,
    create_json_response,
    create_tool_call_response,
    parse_single_tool_call,
)
from .tool_registry import TOOL_REGISTRY
from .workflow_schema import validate_planner_output
from .workflow_factory import build_dynamic_plan


def build_planner_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "supported", "task_type", "region_name", "poi_type",
            "distance_meters", "data_requirements", "workflow", "reason",
        ],
        "properties": {
            "supported": {"type": "boolean"},
            "task_type": {
                "type": "string",
                "enum": [
                    "road_length_around_poi", "administrative_area",
                    "university_count", "adjacent_regions", "unsupported",
                ],
            },
            "region_name": {"type": "string"},
            "poi_type": {"type": "string", "enum": ["university", ""]},
            "distance_meters": {"type": "integer", "minimum": 0, "maximum": 20_000},
            "data_requirements": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "administrative_boundary", "university_pois", "road_network",
                        "neighbor_boundaries",
                    ],
                },
            },
            "workflow": {
                "type": "object",
                "additionalProperties": False,
                "required": ["workflow", "steps"],
                "properties": {
                    "workflow": {"type": "string"},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["tool", "params"],
                            "properties": {
                                "tool": {"type": "string", "enum": list(TOOL_REGISTRY)},
                                "params": {"type": "object", "additionalProperties": True},
                            },
                        },
                    },
                },
            },
            "reason": {"type": "string"},
        },
    }


def _workflow_examples() -> str:
    road = {
        "workflow": "dynamic_road_length_around_poi",
        "steps": [
            {"tool": "download_region_boundary", "params": {"REGION_NAME": "南京市", "OUTPUT": "workspace://raw/region_boundary.gpkg"}},
            {"tool": "validate_dataset", "params": {"INPUT": "workspace://raw/region_boundary.gpkg", "GEOMETRY_TYPE": "polygon"}},
            {"tool": "download_osm_pois", "params": {"BOUNDARY": "workspace://raw/region_boundary.gpkg", "POI_TYPE": "university", "OUTPUT": "workspace://raw/university_pois.gpkg"}},
            {"tool": "validate_dataset", "params": {"INPUT": "workspace://raw/university_pois.gpkg", "GEOMETRY_TYPE": "point"}},
            {"tool": "auto_reproject_layer", "params": {"INPUT": "workspace://raw/university_pois.gpkg", "OUTPUT": "workspace://processed/university_pois_projected.gpkg"}},
            {"tool": "buffer", "params": {"INPUT": "workspace://processed/university_pois_projected.gpkg", "DISTANCE": 500, "SEGMENTS": 12, "DISSOLVE": True, "OUTPUT": "workspace://processed/university_buffers.gpkg"}},
            {"tool": "download_osm_roads", "params": {"AREA": "workspace://processed/university_buffers.gpkg", "POINTS": "workspace://raw/university_pois.gpkg", "REGION_NAME": "南京市", "POI_TYPE": "university", "DISTANCE": 500, "OUTPUT": "workspace://raw/roads.gpkg"}},
            {"tool": "validate_dataset", "params": {"INPUT": "workspace://raw/roads.gpkg", "GEOMETRY_TYPE": "line"}},
            {"tool": "reproject_to_match", "params": {"INPUT": "workspace://raw/roads.gpkg", "REFERENCE": "workspace://processed/university_buffers.gpkg", "OUTPUT": "workspace://processed/roads_projected.gpkg"}},
            {"tool": "clip", "params": {"INPUT": "workspace://processed/roads_projected.gpkg", "OVERLAY": "workspace://processed/university_buffers.gpkg", "OUTPUT": "workspace://processed/roads_clip.gpkg"}},
            {"tool": "sum_line_lengths", "params": {"POLYGONS": "workspace://processed/university_buffers.gpkg", "LINES": "workspace://processed/roads_clip.gpkg", "LEN_FIELD": "road_length", "COUNT_FIELD": "road_count", "OUTPUT": "workspace://result/road_length_around_universities.gpkg"}},
        ],
    }
    area = {
        "workflow": "dynamic_administrative_area",
        "steps": [
            {"tool": "download_region_boundary", "params": {"REGION_NAME": "南京市", "OUTPUT": "workspace://raw/region_boundary.gpkg"}},
            {"tool": "validate_dataset", "params": {"INPUT": "workspace://raw/region_boundary.gpkg", "GEOMETRY_TYPE": "polygon"}},
            {"tool": "auto_reproject_layer", "params": {"INPUT": "workspace://raw/region_boundary.gpkg", "OUTPUT": "workspace://processed/region_projected.gpkg"}},
            {"tool": "calculate_polygon_area", "params": {"INPUT": "workspace://processed/region_projected.gpkg", "AREA_FIELD": "area_sq_km", "OUTPUT": "workspace://result/region_area.gpkg"}},
        ],
    }
    count = {
        "workflow": "dynamic_university_count",
        "steps": [
            {"tool": "download_region_boundary", "params": {"REGION_NAME": "南京市", "OUTPUT": "workspace://raw/region_boundary.gpkg"}},
            {"tool": "validate_dataset", "params": {"INPUT": "workspace://raw/region_boundary.gpkg", "GEOMETRY_TYPE": "polygon"}},
            {"tool": "download_osm_pois", "params": {"BOUNDARY": "workspace://raw/region_boundary.gpkg", "POI_TYPE": "university", "OUTPUT": "workspace://raw/university_pois.gpkg"}},
            {"tool": "validate_dataset", "params": {"INPUT": "workspace://raw/university_pois.gpkg", "GEOMETRY_TYPE": "point"}},
            {"tool": "count_points_in_polygon", "params": {"POLYGONS": "workspace://raw/region_boundary.gpkg", "POINTS": "workspace://raw/university_pois.gpkg", "COUNT_FIELD": "point_count", "OUTPUT": "workspace://result/university_count.gpkg"}},
        ],
    }
    adjacent = build_dynamic_plan("adjacent_regions", "南京市")["workflow"]
    return json.dumps(
        {"road": road, "area": area, "count": count, "adjacent": adjacent},
        ensure_ascii=False,
        indent=2,
    )


def build_system_prompt(extra_context: str | None = None, feedback: str | None = None) -> str:
    context = f"\nRAG context:\n{extra_context}" if extra_context else ""
    correction = f"\nPrevious plan error to correct:\n{feedback}" if feedback else ""
    return f"""
You are the planning node of a GIS agent. Return a complete, executable plan.
Supported tasks:
1. road_length_around_poi: road length around all universities/colleges in a named region.
2. administrative_area: area of one named administrative region.
3. university_count: count OSM university/college POIs in one named region.
4. adjacent_regions: find neighboring regions using the bundled boundary topology fixture.

For nearby/surrounding road length without a distance, use 1000 metres. Convert km to m.
Only poi_type=university is supported. Use the exact workflow templates below. Replace only
REGION_NAME and buffer DISTANCE. Keep every workspace:// path, tool order, DISSOLVE=true,
field name and other parameter unchanged. Never add a URL or local absolute path.

Templates:
{_workflow_examples()}

For unsupported requests set supported=false, task_type=unsupported, empty poi_type,
distance_meters=0, data_requirements=[], workflow={{"workflow":"unsupported","steps":[]}},
and provide a short Chinese reason.
{context}{correction}
""".strip()


def build_input_messages(
    user_query: str,
    extra_context: str | None = None,
    feedback: str | None = None,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": build_system_prompt(extra_context, feedback)},
        {"role": "user", "content": user_query},
    ]


def _native_plan(messages: list[dict[str, str]], schema: dict, model: str | None) -> dict:
    submit_tool = {
        "type": "function",
        "function": {
            "name": "submit_gis_plan",
            "description": "Submit one complete GIS workflow for validation and execution.",
            "parameters": schema,
        },
    }
    message = create_tool_call_response(
        messages,
        [submit_tool],
        model=model,
        tool_choice={"type": "function", "function": {"name": "submit_gis_plan"}},
    )
    plan, raw_call = parse_single_tool_call(message, "submit_gis_plan")
    plan["planner_mode"] = "native_tool_calling"
    plan["native_tool_call"] = raw_call
    return plan


def plan_workflow_with_llm(
    user_query: str,
    model: str | None = None,
    extra_context: str | None = None,
    feedback: str | None = None,
) -> dict:
    if "相邻的地级行政区" in user_query:
        region = user_query.split("查询", 1)[-1].split("相邻", 1)[0].strip()
        plan = build_dynamic_plan("adjacent_regions", region)
        plan["planner_mode"] = "deterministic_context_route"
        validate_planner_output(plan)
        return plan
    schema = build_planner_output_schema()
    messages = build_input_messages(user_query, extra_context, feedback)
    mode = env_str("PLANNER_MODE", "native_tool_calling").lower()
    try:
        if mode == "native_tool_calling":
            plan = _native_plan(messages, schema, model)
        else:
            plan = create_json_response(messages, schema, model=model)
            plan["planner_mode"] = "json"
    except LLMClientError:
        if mode != "native_tool_calling" or not env_bool("PLANNER_ALLOW_JSON_FALLBACK", True):
            raise
        plan = create_json_response(messages, schema, model=model)
        plan["planner_mode"] = "json_fallback"
    validate_planner_output(plan)
    return plan


def plan_workflow(user_query: str) -> dict:
    return plan_workflow_with_llm(user_query)
