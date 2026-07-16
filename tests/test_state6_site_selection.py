import tempfile
import unittest
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, Point, box

from geoai_agent.python_gis_tools import multi_criteria_site_selection
from geoai_agent.workflow_factory import build_dynamic_plan
from geoai_agent.workflow_schema import validate_planner_output


class State6SiteSelectionTests(unittest.TestCase):
    def test_site_selection_ranks_and_writes_candidate_cells(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            crs = "EPSG:3857"
            boundary = gpd.GeoDataFrame(
                [{"name": "demo", "geometry": box(0, 0, 12000, 12000)}], crs=crs
            )
            facilities = gpd.GeoDataFrame(
                [{"geometry": Point(3000, 3000)}, {"geometry": Point(9000, 9000)}],
                crs=crs,
            )
            roads = gpd.GeoDataFrame(
                [
                    {"geometry": LineString([(0, 6000), (12000, 6000)])},
                    {"geometry": LineString([(6000, 0), (6000, 12000)])},
                ],
                crs=crs,
            )
            boundary_path = root / "boundary.gpkg"
            facilities_path = root / "facilities.gpkg"
            roads_path = root / "roads.gpkg"
            output_path = root / "candidates.gpkg"
            boundary.to_file(boundary_path, driver="GPKG")
            facilities.to_file(facilities_path, driver="GPKG")
            roads.to_file(roads_path, driver="GPKG")

            metrics = multi_criteria_site_selection(
                {
                    "BOUNDARY": str(boundary_path),
                    "FACILITIES": str(facilities_path),
                    "ROADS": str(roads_path),
                    "CELL_SIZE": 3000,
                    "TOP_N": 5,
                    "OUTPUT": str(output_path),
                }
            )
            result = gpd.read_file(output_path)
            self.assertEqual(len(result), 5)
            self.assertEqual(result["rank"].tolist(), [1, 2, 3, 4, 5])
            self.assertTrue(result["site_score"].is_monotonic_decreasing)
            self.assertEqual(metrics["candidate_count"], 5)

    def test_state6_workflow_factory_is_schema_valid(self):
        plan = build_dynamic_plan(
            "multi_criteria_site_selection", "南京市", distance_meters=3000
        )
        validate_planner_output(plan)
        self.assertEqual(plan["workflow"]["steps"][-1]["tool"], "multi_criteria_site_selection")


if __name__ == "__main__":
    unittest.main()
