from __future__ import annotations

from typing import Any

from .tool_registry import get_tool_config


TASK_WORKFLOW_NAMES = {
    "road_length_around_poi": "dynamic_road_length_around_poi",
    "administrative_area": "dynamic_administrative_area",
    "university_count": "dynamic_university_count",
    "adjacent_regions": "fixture_adjacent_regions",
}

WORKFLOW_TOOL_SEQUENCES = {
    "dynamic_road_length_around_poi": [
        "download_region_boundary",
        "validate_dataset",
        "download_osm_pois",
        "validate_dataset",
        "auto_reproject_layer",
        "buffer",
        "download_osm_roads",
        "validate_dataset",
        "reproject_to_match",
        "clip",
        "sum_line_lengths",
    ],
    "dynamic_administrative_area": [
        "download_region_boundary",
        "validate_dataset",
        "auto_reproject_layer",
        "calculate_polygon_area",
    ],
    "dynamic_university_count": [
        "download_region_boundary",
        "validate_dataset",
        "download_osm_pois",
        "validate_dataset",
        "count_points_in_polygon",
    ],
    "fixture_adjacent_regions": [
        "load_neighbor_boundaries",
        "validate_dataset",
        "select_feature_by_attribute",
        "find_adjacent_polygons",
    ],
}

PATH_PARAM_NAMES = {
    "INPUT", "OUTPUT", "OVERLAY", "POLYGONS", "LINES", "POINTS",
    "TARGET", "REFERENCE", "AREA", "BOUNDARY",
}


class WorkflowSchemaError(ValueError):
    pass


def _require_dict(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        raise WorkflowSchemaError(f"{name} must be an object.")
    return value


def _require_string(value: object, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        suffix = "a string" if allow_empty else "a non-empty string"
        raise WorkflowSchemaError(f"{name} must be {suffix}.")
    return value


def _validate_param_type(name: str, value: Any, schema: dict, label: str) -> None:
    expected = schema.get("type")
    type_map = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
    }
    if expected in type_map and not isinstance(value, type_map[expected]):
        raise WorkflowSchemaError(f"{label}.{name} must be {expected}.")
    if expected == "integer" and isinstance(value, bool):
        raise WorkflowSchemaError(f"{label}.{name} must be integer.")
    if "enum" in schema and value not in schema["enum"]:
        raise WorkflowSchemaError(f"{label}.{name} must be one of {schema['enum']}.")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise WorkflowSchemaError(f"{label}.{name} is below minimum.")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise WorkflowSchemaError(f"{label}.{name} must be greater than minimum.")


def validate_task_schema(task: dict, step_index: int | None = None, strict: bool = True) -> None:
    label = f"workflow.steps[{step_index - 1}]" if step_index else "task"
    task = _require_dict(task, label)
    tool_name = _require_string(task.get("tool"), f"{label}.tool")
    params = _require_dict(task.get("params"), f"{label}.params")
    try:
        config = get_tool_config(tool_name)
    except ValueError as exc:
        raise WorkflowSchemaError(str(exc)) from exc
    missing = [name for name in config["required_params"] if params.get(name) in (None, "")]
    if missing:
        raise WorkflowSchemaError(f"{label} missing required params for '{tool_name}': {missing}")
    allowed = set(config["required_params"]) | set(config["optional_params"])
    if strict:
        unknown = sorted(set(params) - allowed)
        if unknown:
            raise WorkflowSchemaError(f"{label} contains unknown params: {unknown}")
    for name, value in params.items():
        if name in config["properties"]:
            _validate_param_type(name, value, config["properties"][name], label)
        if name in PATH_PARAM_NAMES:
            if not isinstance(value, str) or not value.startswith("workspace://"):
                raise WorkflowSchemaError(
                    f"{label}.{name} must use a workspace:// task-scoped path."
                )


def validate_workflow_schema(workflow: dict, strict: bool = True) -> None:
    workflow = _require_dict(workflow, "workflow")
    name = _require_string(workflow.get("workflow"), "workflow.workflow")
    steps = workflow.get("steps")
    if not isinstance(steps, list) or not steps:
        raise WorkflowSchemaError("workflow.steps must be a non-empty list.")
    for index, task in enumerate(steps, start=1):
        validate_task_schema(task, index, strict)
    expected = WORKFLOW_TOOL_SEQUENCES.get(name)
    if expected is None:
        raise WorkflowSchemaError(f"Unsupported workflow name: {name}")
    actual = [step["tool"] for step in steps]
    if actual != expected:
        raise WorkflowSchemaError(f"Workflow '{name}' requires tools {expected}, got {actual}.")


def validate_planner_output(plan: dict, strict: bool = True) -> None:
    plan = _require_dict(plan, "planner output")
    if not isinstance(plan.get("supported"), bool):
        raise WorkflowSchemaError("planner output.supported must be boolean.")
    task_type = _require_string(plan.get("task_type"), "planner output.task_type")
    region_name = _require_string(
        plan.get("region_name"), "planner output.region_name", allow_empty=True,
    )
    poi_type = _require_string(plan.get("poi_type"), "planner output.poi_type", allow_empty=True)
    distance = plan.get("distance_meters")
    if not isinstance(distance, int) or isinstance(distance, bool) or distance < 0:
        raise WorkflowSchemaError("planner output.distance_meters must be a non-negative integer.")
    requirements = plan.get("data_requirements")
    if not isinstance(requirements, list) or not all(isinstance(item, str) for item in requirements):
        raise WorkflowSchemaError("planner output.data_requirements must be a string array.")

    if not plan["supported"]:
        if task_type != "unsupported":
            raise WorkflowSchemaError("Unsupported plans must use task_type='unsupported'.")
        _require_string(plan.get("reason"), "planner output.reason")
        return

    if task_type not in TASK_WORKFLOW_NAMES:
        raise WorkflowSchemaError(f"Unsupported task_type: {task_type}")
    if not region_name.strip():
        raise WorkflowSchemaError(f"{task_type} requires region_name.")
    if task_type == "road_length_around_poi":
        if poi_type != "university" or distance <= 0:
            raise WorkflowSchemaError(
                "road_length_around_poi requires poi_type='university' and positive distance."
            )
        required_data = {"administrative_boundary", "university_pois", "road_network"}
    elif task_type == "university_count":
        if poi_type != "university" or distance != 0:
            raise WorkflowSchemaError("university_count requires university and distance=0.")
        required_data = {"administrative_boundary", "university_pois"}
    elif task_type == "administrative_area":
        if poi_type or distance != 0:
            raise WorkflowSchemaError("administrative_area requires empty poi_type and distance=0.")
        required_data = {"administrative_boundary"}
    else:
        if poi_type or distance != 0:
            raise WorkflowSchemaError("adjacent_regions requires empty poi_type and distance=0.")
        required_data = {"neighbor_boundaries"}
    if set(requirements) != required_data:
        raise WorkflowSchemaError(
            f"{task_type} requires data_requirements={sorted(required_data)}."
        )

    workflow = plan.get("workflow")
    validate_workflow_schema(workflow, strict=strict)
    expected_name = TASK_WORKFLOW_NAMES[task_type]
    if workflow["workflow"] != expected_name:
        raise WorkflowSchemaError(f"{task_type} requires workflow '{expected_name}'.")
    first_params = workflow["steps"][0]["params"]
    if first_params.get("REGION_NAME") != region_name:
        raise WorkflowSchemaError("Workflow region must match planner region_name.")
    if task_type == "road_length_around_poi":
        buffer_step = next(step for step in workflow["steps"] if step["tool"] == "buffer")
        if int(buffer_step["params"]["DISTANCE"]) != distance:
            raise WorkflowSchemaError("Buffer distance must match planner distance_meters.")
        if buffer_step["params"].get("DISSOLVE") is not True:
            raise WorkflowSchemaError("POI buffers must use DISSOLVE=true to avoid double counting.")
        road_step = next(step for step in workflow["steps"] if step["tool"] == "download_osm_roads")
        if int(road_step["params"]["DISTANCE"]) != distance:
            raise WorkflowSchemaError("Road query distance must match planner distance_meters.")
        if road_step["params"].get("REGION_NAME") != region_name:
            raise WorkflowSchemaError("Road query region must match planner region_name.")


def extract_workflow_tools(workflow: dict) -> list[str]:
    validate_workflow_schema(workflow)
    return [step["tool"] for step in workflow["steps"]]
