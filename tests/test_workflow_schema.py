from __future__ import annotations

import copy
import unittest

from geoai_agent.workflow_factory import build_dynamic_plan
from geoai_agent.workflow_schema import WorkflowSchemaError, validate_planner_output


class WorkflowSchemaTests(unittest.TestCase):
    def test_all_supported_factories_are_valid(self) -> None:
        plans = [
            build_dynamic_plan("road_length_around_poi", "南京市", distance_meters=500),
            build_dynamic_plan("administrative_area", "南京市"),
            build_dynamic_plan("university_count", "南京市"),
            build_dynamic_plan("adjacent_regions", "南京市"),
        ]
        for plan in plans:
            with self.subTest(task_type=plan["task_type"]):
                validate_planner_output(plan)

    def test_rejects_non_workspace_path(self) -> None:
        plan = build_dynamic_plan("administrative_area", "南京市")
        plan["workflow"]["steps"][0]["params"]["OUTPUT"] = "C:/unsafe.gpkg"
        with self.assertRaises(WorkflowSchemaError):
            validate_planner_output(plan)

    def test_rejects_non_dissolved_buffers(self) -> None:
        plan = build_dynamic_plan("road_length_around_poi", "南京市", distance_meters=500)
        plan["workflow"]["steps"][5]["params"]["DISSOLVE"] = False
        with self.assertRaises(WorkflowSchemaError):
            validate_planner_output(plan)

    def test_rejects_wrong_tool_order(self) -> None:
        plan = build_dynamic_plan("university_count", "南京市")
        plan["workflow"]["steps"][2:4] = reversed(plan["workflow"]["steps"][2:4])
        with self.assertRaises(WorkflowSchemaError):
            validate_planner_output(plan)


if __name__ == "__main__":
    unittest.main()
