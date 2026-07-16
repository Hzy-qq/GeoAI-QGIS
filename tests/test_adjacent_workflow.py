from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

from geoai_agent.executor import execute_workflow
from geoai_agent.result_summarizer import summarize_workflow_result
from geoai_agent.task_workspace import TaskWorkspace
from geoai_agent.workflow_evaluator import evaluate_workflow_result
from geoai_agent.workflow_factory import build_dynamic_plan
from geoai_agent.workflow_schema import validate_planner_output


class AdjacentWorkflowTests(unittest.TestCase):
    @staticmethod
    def _ensure_synthetic_fixture() -> tuple[Path, bool]:
        fixture = (
            Path(__file__).resolve().parents[1]
            / "data"
            / "fixtures"
            / "nanjing_neighbor_cities.gpkg"
        )
        if fixture.exists():
            return fixture, False
        fixture.parent.mkdir(parents=True, exist_ok=True)
        gpd.GeoDataFrame(
            {"region_name": ["南京市", "镇江市", "扬州市", "马鞍山市"]},
            geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1), box(0, 1, 1, 2), box(-1, 0, 0, 1)],
            crs="EPSG:4326",
        ).to_file(fixture, driver="GPKG")
        return fixture, True

    def test_bundled_nanjing_topology_workflow(self) -> None:
        fixture, generated = self._ensure_synthetic_fixture()
        workspace = TaskWorkspace.create(f"test-adjacent-{uuid.uuid4().hex}")
        try:
            plan = build_dynamic_plan("adjacent_regions", "南京市")
            validate_planner_output(plan)
            execution = execute_workflow(plan["workflow"], workspace)
            self.assertTrue(execution["success"], execution.get("error_message"))
            evaluation = evaluate_workflow_result(plan["workflow"], execution, workspace)
            self.assertTrue(evaluation["passed"], evaluation.get("issues"))
            summary = summarize_workflow_result(
                "查询南京市相邻的地级行政区", plan, workspace, use_llm=False,
            )
            self.assertGreater(summary["adjacent_count"], 0)
            self.assertIn("镇江市", summary["adjacent_names"])
        finally:
            shutil.rmtree(workspace.root, ignore_errors=True)
            if generated:
                fixture.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
