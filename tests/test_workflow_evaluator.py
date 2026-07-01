from __future__ import annotations

import unittest

import geopandas as gpd
from shapely.geometry import Polygon

from geoai_agent.task_workspace import TaskWorkspace
from geoai_agent.workflow_evaluator import evaluate_workflow_result
from geoai_agent.workflow_factory import build_dynamic_plan


class WorkflowEvaluatorTests(unittest.TestCase):
    def test_accepts_valid_road_result(self) -> None:
        workspace = TaskWorkspace.create("unit_evaluator")
        plan = build_dynamic_plan("road_length_around_poi", "南京市", distance_meters=500)
        output = workspace.resolve(plan["workflow"]["steps"][-1]["params"]["OUTPUT"])
        gpd.GeoDataFrame(
            {"road_length": [1000.0], "road_count": [5]},
            geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])],
            crs="EPSG:32650",
        ).to_file(output, driver="GPKG")
        result = evaluate_workflow_result(plan["workflow"], {"success": True}, workspace)
        self.assertTrue(result["passed"])


if __name__ == "__main__":
    unittest.main()
