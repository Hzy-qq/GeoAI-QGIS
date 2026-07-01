from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point, Polygon

from geoai_agent.crs_manager import auto_reproject_layer
from geoai_agent.data_validation import validate_dataset


class DataToolTests(unittest.TestCase):
    def test_validation_and_auto_reprojection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "points.gpkg"
            output = Path(temp) / "projected.gpkg"
            gpd.GeoDataFrame(
                {"name": ["A", "B"]},
                geometry=[Point(118.8, 32.0), Point(118.9, 32.1)],
                crs="EPSG:4326",
            ).to_file(source, driver="GPKG")
            result = validate_dataset({"INPUT": str(source), "GEOMETRY_TYPE": "point"})
            self.assertTrue(result["valid"])
            projected = auto_reproject_layer({"INPUT": str(source), "OUTPUT": str(output)})
            self.assertEqual(projected["target_crs"], "EPSG:32650")

    def test_rejects_wrong_geometry_family(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "polygon.gpkg"
            gpd.GeoDataFrame(
                {"name": ["region"]},
                geometry=[Polygon([(118, 31), (119, 31), (119, 32), (118, 32)])],
                crs="EPSG:4326",
            ).to_file(source, driver="GPKG")
            with self.assertRaises(RuntimeError):
                validate_dataset({"INPUT": str(source), "GEOMETRY_TYPE": "point"})


if __name__ == "__main__":
    unittest.main()
