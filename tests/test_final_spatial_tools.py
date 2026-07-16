from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point, box

from geoai_agent.context_resolver import classify_task
from geoai_agent.python_gis_tools import (
    multi_ring_service_analysis,
    nearest_neighbor_analysis,
    service_gap_analysis,
)
from geoai_agent.workflow_factory import build_dynamic_plan
from geoai_agent.workflow_schema import validate_planner_output


class FinalSpatialToolsTests(unittest.TestCase):
    def test_new_queries_route_to_deterministic_workflows(self) -> None:
        cases = {
            "分析南京市医院1公里服务盲区": "service_gap_analysis",
            "分析南京市医院多级服务圈": "multi_ring_service_analysis",
            "分析南京市医院最近邻和平均间距": "poi_nearest_neighbor",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(classify_task(query), expected)
                distance = 1000 if expected == "service_gap_analysis" else 0
                plan = build_dynamic_plan(
                    expected,
                    "南京市",
                    poi_type="hospital",
                    distance_meters=distance,
                )
                validate_planner_output(plan)

    def test_new_geometry_algorithms_write_auditable_layers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boundary_path = root / "boundary.gpkg"
            points_path = root / "points.gpkg"
            coverage_path = root / "coverage.gpkg"
            gpd.GeoDataFrame(
                {"name": ["test"]},
                geometry=[box(0, 0, 10_000, 10_000)],
                crs="EPSG:3857",
            ).to_file(boundary_path, driver="GPKG")
            points = gpd.GeoDataFrame(
                {"name": ["A", "B", "C"]},
                geometry=[Point(1000, 1000), Point(2000, 1000), Point(8000, 8000)],
                crs="EPSG:3857",
            )
            points.to_file(points_path, driver="GPKG")
            gpd.GeoDataFrame(
                {"name": ["coverage"]},
                geometry=[Point(5000, 5000).buffer(2500)],
                crs="EPSG:3857",
            ).to_file(coverage_path, driver="GPKG")

            nearest = nearest_neighbor_analysis({
                "INPUT": str(points_path),
                "DISTANCE_FIELD": "nearest_neighbor_m",
                "OUTPUT": str(root / "nearest.gpkg"),
            })
            self.assertEqual(nearest["feature_count"], 3)
            self.assertAlmostEqual(nearest["minimum_distance_m"], 1000.0)

            gap = service_gap_analysis({
                "BOUNDARY": str(boundary_path),
                "COVERAGE": str(coverage_path),
                "DISTANCE": 2500,
                "OUTPUT": str(root / "gap.gpkg"),
            })
            self.assertGreater(gap["coverage_rate_pct"], 0)
            self.assertGreater(gap["uncovered_sq_km"], 0)

            rings = multi_ring_service_analysis({
                "BOUNDARY": str(boundary_path),
                "POINTS": str(points_path),
                "DISTANCES": "500,1000,2000",
                "OUTPUT": str(root / "rings.gpkg"),
            })
            self.assertEqual(rings["ring_count"], 3)
            ring_layer = gpd.read_file(root / "rings.gpkg").sort_values("distance_m")
            self.assertTrue(ring_layer["coverage_sq_km"].is_monotonic_increasing)


if __name__ == "__main__":
    unittest.main()
