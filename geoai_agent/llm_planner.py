from __future__ import annotations

import json
from typing import Any

from .llm_client import create_json_response
from .tool_registry import TOOL_REGISTRY
from .workflow_schema import validate_planner_output


def build_planner_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["supported", "distance_meters", "workflow", "reason"],
        "properties": {
            "supported": {"type": "boolean"},
            "distance_meters": {
                "type": "integer",
                "description": "Buffer distance in meters. Use 0 for unsupported tasks.",
            },
            "reason": {
                "type": "string",
                "description": "Empty string for supported tasks; explanation for unsupported tasks.",
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
                                "tool": {
                                    "type": "string",
                                    "enum": list(TOOL_REGISTRY.keys()),
                                },
                                "params": {
                                    "type": "object",
                                    "additionalProperties": True,
                                },
                            },
                        },
                    },
                },
            },
        },
    }


def build_system_prompt(extra_context: str | None = None) -> str:
    tool_specs = json.dumps(TOOL_REGISTRY, ensure_ascii=False, indent=2)
    context_block = ""
    if extra_context:
        context_block = f"""

Retrieved context from the GeoAI knowledge base:
{extra_context}
"""

    return f"""
You are a GIS workflow planner for GeoAI.

Your only job is to convert a user's natural-language GIS request into planner
JSON. You must not execute code, invent tools, or call QGIS.

Available tools are:
{tool_specs}
{context_block}

Current supported task:
- Road length statistics inside a buffer around places.
- Generate exactly three steps for this task:
  1. buffer
  2. clip
  3. sum_line_lengths

Use these fixed input datasets:
- places: data/processed/places.gpkg
- roads: data/processed/roads.gpkg

Distance rules:
- Convert kilometers to meters.
- If the user asks for nearby/surrounding road length but gives no distance,
  use 1000 meters.

For a supported road length task with distance N, use these output paths:
- outputs/places_buffer_Nm.gpkg
- outputs/roads_clip_Nm.gpkg
- outputs/buffer_with_road_length_Nm.gpkg

For sum_line_lengths, use:
- LEN_FIELD: road_length
- COUNT_FIELD: road_count

If the task is unsupported, return:
- supported: false
- distance_meters: 0
- workflow: {{"workflow": "unsupported", "steps": []}}
- reason: a short Chinese explanation

Return only planner JSON that matches the provided schema.
""".strip()


def build_input_messages(user_query: str, extra_context: str | None = None) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": build_system_prompt(extra_context)},
        {"role": "user", "content": user_query},
    ]


def plan_workflow_with_llm(
    user_query: str,
    model: str | None = None,
    extra_context: str | None = None,
) -> dict:
    plan = create_json_response(
        build_input_messages(user_query, extra_context),
        build_planner_output_schema(),
        model=model,
    )
    validate_planner_output(plan)
    return plan


def plan_workflow(user_query: str) -> dict:
    return plan_workflow_with_llm(user_query)
