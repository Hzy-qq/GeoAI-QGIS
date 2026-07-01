from __future__ import annotations

from typing import Any

from .dataset_catalog import get_dataset_spec


def resolve_data_requirements(plan: dict[str, Any]) -> list[dict[str, Any]]:
    resolved = []
    for dataset_id in plan.get("data_requirements", []):
        spec = get_dataset_spec(dataset_id)
        resolved.append({
            "dataset_id": dataset_id,
            "source_id": spec["source_id"],
            "license": spec["license"],
            "geometry": spec["geometry"],
            "status": "required",
        })
    return resolved
