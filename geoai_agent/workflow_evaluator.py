from __future__ import annotations

from pathlib import Path
from typing import Any

from .task_workspace import TaskWorkspace


REQUIRED_RESULT_FIELDS = {
    "dynamic_road_length_around_poi": {"road_length", "road_count"},
    "dynamic_administrative_area": {"area_sq_km"},
    "dynamic_university_count": {"point_count"},
    "dynamic_poi_count": {"point_count"},
    "dynamic_poi_service_area": {"coverage_sq_km"},
    "dynamic_poi_density": {"point_count", "density_per_sq_km", "area_sq_km"},
    "dynamic_poi_road_accessibility": {"nearest_road_m"},
    "dynamic_road_density": {
        "road_length_km", "density_km_per_sq_km", "area_sq_km",
    },
    "fixture_adjacent_regions": set(),
    "dynamic_multi_criteria_site_selection": {"rank", "site_score", "area_sq_km"},
    "dynamic_advanced_site_selection": {
        "rank", "site_score", "area_sq_km", "road_distance_m",
        "transit_distance_m", "water_distance_m",
    },
}


def evaluate_workflow_result(
    workflow: dict,
    execution_trace: dict,
    workspace: TaskWorkspace,
) -> dict[str, Any]:
    issues: list[str] = []
    if not execution_trace.get("success"):
        issues.append(execution_trace.get("error_message") or "Workflow execution failed.")
        return {"passed": False, "issues": issues}
    final_output = workflow["steps"][-1]["params"].get("OUTPUT")
    if not final_output:
        return {"passed": False, "issues": ["Final workflow step has no OUTPUT."]}
    path = workspace.resolve(final_output)
    if not path.exists():
        return {"passed": False, "issues": [f"Result file does not exist: {path}"]}
    try:
        import geopandas as gpd

        result = gpd.read_file(path)
    except Exception as exc:
        return {"passed": False, "issues": [f"Result layer is unreadable: {exc}"]}
    if result.empty:
        issues.append("Result layer is empty.")
    if workflow["workflow"] == "fixture_adjacent_regions" and "region_name" not in result.columns:
        issues.append("Adjacent-region result is missing region_name.")
    required = REQUIRED_RESULT_FIELDS.get(workflow["workflow"], set())
    missing = sorted(required - set(result.columns))
    if missing:
        issues.append(f"Result is missing required fields: {missing}")
    for field in required:
        if field in result.columns:
            values = result[field].dropna()
            if values.empty or (values < 0).any():
                issues.append(f"Result field {field} contains no valid non-negative value.")
    return {
        "passed": not issues,
        "issues": issues,
        "result_file": str(path),
        "feature_count": int(len(result)),
        "required_fields": sorted(required),
    }
