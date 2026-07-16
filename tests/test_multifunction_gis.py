from __future__ import annotations

import tempfile
import unittest
import shutil
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, Point, box

from geoai_agent.context_resolver import resolve_conversation_context
from geoai_agent.llm_planner import plan_workflow_with_llm
from geoai_agent.python_gis_tools import (
    advanced_site_selection,
    line_density_grid,
    nearest_distance_to_features,
    point_density_grid,
)
from geoai_agent.result_summarizer import build_deterministic_answer, summarize_workflow_result
from geoai_agent.task_workspace import TaskWorkspace
from geoai_agent.workflow_factory import build_dynamic_plan
from geoai_agent.workflow_schema import validate_planner_output


class MultiFunctionPlannerTests(unittest.TestCase):
    def test_all_new_workflow_factories_pass_schema_validation(self) -> None:
        plans = [
            build_dynamic_plan("poi_count", "南京市", poi_type="hospital"),
            build_dynamic_plan(
                "poi_service_area", "南京市", poi_type="hospital", distance_meters=1000,
            ),
            build_dynamic_plan("poi_density", "南京市", poi_type="subway_station"),
            build_dynamic_plan("poi_road_accessibility", "南京市", poi_type="hospital"),
            build_dynamic_plan("road_density", "南京市"),
            build_dynamic_plan("advanced_site_selection", "南京市", distance_meters=1000),
        ]
        for plan in plans:
            with self.subTest(task_type=plan["task_type"]):
                validate_planner_output(plan)

    def test_every_road_workflow_uses_main_roads(self) -> None:
        plans = [
            build_dynamic_plan(
                "road_length_around_poi", "南京市", poi_type="university",
                distance_meters=1000,
            ),
            build_dynamic_plan("poi_road_accessibility", "南京市", poi_type="hospital"),
            build_dynamic_plan("road_density", "南京市"),
            build_dynamic_plan(
                "multi_criteria_site_selection", "南京市", distance_meters=3000,
            ),
            build_dynamic_plan("advanced_site_selection", "南京市", distance_meters=1000),
        ]
        for plan in plans:
            road_steps = [
                step for step in plan["workflow"]["steps"]
                if step["tool"] in {"download_osm_roads", "download_osm_roads_in_area"}
            ]
            with self.subTest(task_type=plan["task_type"]):
                self.assertTrue(road_steps)
                self.assertTrue(
                    all(step["params"]["ROAD_LEVEL"] == "main" for step in road_steps)
                )

    def test_road_answer_discloses_main_road_scope(self) -> None:
        answer = build_deterministic_answer(
            "统计道路长度",
            {
                "result_type": "road_length_around_poi",
                "distance_meters": 1000,
                "region_name": "南京市",
                "road_length_km": 12.3,
                "road_count": 8,
                "result_file": "result.gpkg",
                "road_scope_notice": "仅统计主要道路，不含支路和生活道路。",
            },
        )
        self.assertIn("主要道路总长度", answer)
        self.assertIn("不含支路和生活道路", answer)

    def test_supported_query_uses_deterministic_capability_route(self) -> None:
        plan = plan_workflow_with_llm("分析南京市医院1公里服务覆盖范围")
        self.assertEqual(plan["task_type"], "poi_service_area")
        self.assertEqual(plan["poi_type"], "hospital")
        self.assertEqual(plan["distance_meters"], 1000)
        self.assertEqual(plan["planner_mode"], "deterministic_capability_route")

    def test_nearest_main_road_query_uses_deterministic_capability_route(self) -> None:
        plan = plan_workflow_with_llm("分析南京市医院到最近主要道路的距离")
        self.assertEqual(plan["task_type"], "poi_road_accessibility")
        self.assertEqual(plan["poi_type"], "hospital")
        self.assertEqual(plan["planner_mode"], "deterministic_capability_route")
        validate_planner_output(plan)

    def test_follow_up_inherits_intent_region_and_poi_then_changes_distance(self) -> None:
        memory = {
            "current_region": "南京市",
            "previous_task_type": "poi_service_area",
            "previous_poi_type": "hospital",
            "previous_distance_meters": 1000,
        }
        result = resolve_conversation_context("把范围改为2公里", memory)
        self.assertEqual(result["task_type"], "poi_service_area")
        self.assertEqual(result["region_name"], "南京市")
        self.assertEqual(result["resolution_source"], "memory")
        self.assertIn("医院2000米", result["resolved_query"])

    def test_region_extraction_does_not_swallow_request_prefix(self) -> None:
        result = resolve_conversation_context(
            "在南京市选择新校区，要求距主干路不超过1公里、靠近地铁并避开水域", {}
        )
        self.assertEqual(result["region_name"], "南京市")
        self.assertEqual(result["task_type"], "advanced_site_selection")

    def test_follow_up_can_change_region_and_poi_without_repeating_intent(self) -> None:
        memory = {
            "current_region": "南京市",
            "previous_task_type": "poi_density",
            "previous_poi_type": "hospital",
        }
        changed_region = resolve_conversation_context("把区域换成上海市", memory)
        changed_poi = resolve_conversation_context("换成公园", memory)
        self.assertEqual(changed_region["region_name"], "上海市")
        self.assertIn("上海市医院", changed_region["resolved_query"])
        self.assertEqual(changed_poi["region_name"], "南京市")
        self.assertIn("南京市公园", changed_poi["resolved_query"])


class MultiFunctionGisToolTests(unittest.TestCase):
    def _write_inputs(self, root: Path) -> dict[str, Path]:
        crs = "EPSG:3857"
        layers = {
            "boundary": gpd.GeoDataFrame(
                [{"name": "demo", "geometry": box(0, 0, 10000, 10000)}], crs=crs,
            ),
            "points": gpd.GeoDataFrame(
                [{"geometry": Point(1500, 1500)}, {"geometry": Point(6500, 6500)}],
                crs=crs,
            ),
            "transit": gpd.GeoDataFrame(
                [{"geometry": Point(2500, 2500)}, {"geometry": Point(7500, 7500)}],
                crs=crs,
            ),
            "roads": gpd.GeoDataFrame(
                [
                    {"geometry": LineString([(0, 5000), (10000, 5000)])},
                    {"geometry": LineString([(5000, 0), (5000, 10000)])},
                ],
                crs=crs,
            ),
            "water": gpd.GeoDataFrame(
                [{"geometry": box(0, 0, 800, 800)}], crs=crs,
            ),
        }
        paths = {}
        for name, layer in layers.items():
            path = root / f"{name}.gpkg"
            layer.to_file(path, driver="GPKG")
            paths[name] = path
        return paths

    def test_density_and_nearest_distance_tools_write_expected_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = self._write_inputs(root)
            point_output = root / "point_density.gpkg"
            line_output = root / "line_density.gpkg"
            nearest_output = root / "nearest.gpkg"

            point_metrics = point_density_grid({
                "BOUNDARY": str(paths["boundary"]), "POINTS": str(paths["points"]),
                "CELL_SIZE": 5000, "OUTPUT": str(point_output),
            })
            line_metrics = line_density_grid({
                "BOUNDARY": str(paths["boundary"]), "LINES": str(paths["roads"]),
                "CELL_SIZE": 5000, "OUTPUT": str(line_output),
            })
            nearest_metrics = nearest_distance_to_features({
                "INPUT": str(paths["points"]), "TARGET": str(paths["roads"]),
                "DISTANCE_FIELD": "nearest_road_m", "OUTPUT": str(nearest_output),
            })

            point_result = gpd.read_file(point_output)
            line_result = gpd.read_file(line_output)
            nearest_result = gpd.read_file(nearest_output)
            self.assertEqual(point_metrics["point_count"], 2)
            self.assertIn("density_per_sq_km", point_result.columns)
            self.assertGreater(line_metrics["road_length_km"], 0)
            self.assertIn("density_km_per_sq_km", line_result.columns)
            self.assertEqual(nearest_metrics["feature_count"], 2)
            self.assertIn("nearest_road_m", nearest_result.columns)

    def test_advanced_site_selection_applies_constraints_and_ranks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = self._write_inputs(root)
            output = root / "advanced_candidates.gpkg"
            metrics = advanced_site_selection({
                "BOUNDARY": str(paths["boundary"]),
                "FACILITIES": str(paths["points"]),
                "TRANSIT": str(paths["transit"]),
                "ROADS": str(paths["roads"]),
                "WATER": str(paths["water"]),
                "CELL_SIZE": 2000,
                "TOP_N": 6,
                "MAX_ROAD_DISTANCE": 5000,
                "MAX_TRANSIT_DISTANCE": 5000,
                "MIN_WATER_DISTANCE": 100,
                "OUTPUT": str(output),
            })
            result = gpd.read_file(output)
            self.assertGreater(metrics["candidate_count"], 0)
            self.assertEqual(result["rank"].tolist(), list(range(1, len(result) + 1)))
            self.assertTrue(result["site_score"].is_monotonic_decreasing)
            self.assertTrue((result["water_distance_m"] >= 100).all())

    def test_workspace_resolves_all_multi_layer_parameters(self) -> None:
        workspace = TaskWorkspace.create("test-multilayer-params")
        try:
            resolved = workspace.resolve_params({
                "FACILITIES": "workspace://raw/facilities.gpkg",
                "TRANSIT": "workspace://raw/transit.gpkg",
                "ROADS": "workspace://raw/roads.gpkg",
                "WATER": "workspace://raw/water.gpkg",
            })
            for value in resolved.values():
                self.assertTrue(Path(value).is_absolute())
                self.assertIn(str(workspace.root), value)
        finally:
            shutil.rmtree(workspace.root, ignore_errors=True)

    def test_new_workflow_result_has_deterministic_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "result").mkdir()
            result_path = root / "result" / "poi_density_grid.gpkg"
            gpd.GeoDataFrame(
                [
                    {
                        "point_count": 3,
                        "area_sq_km": 25.0,
                        "density_per_sq_km": 0.12,
                        "geometry": box(0, 0, 1, 1),
                    }
                ],
                crs="EPSG:4326",
            ).to_file(result_path, driver="GPKG")
            workspace = TaskWorkspace(task_id="summary-test", root=root)
            plan = build_dynamic_plan("poi_density", "南京市", poi_type="hospital")
            summary = summarize_workflow_result(
                "分析南京市医院密度", plan, workspace, use_llm=False,
            )
            self.assertIsNotNone(summary)
            self.assertEqual(summary["result_type"], "poi_density")
            self.assertEqual(summary["point_count"], 3)
            self.assertIn("医院", summary["answer"])


if __name__ == "__main__":
    unittest.main()
