from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
from shapely.geometry import Polygon

from geoai_agent.data_acquisition import download_osm_pois, download_osm_roads, download_region_boundary


BOUNDARY_RESPONSE = {
    "type": "FeatureCollection",
    "features": [{
        "type": "Feature",
        "properties": {"display_name": "测试市", "importance": 1, "osm_id": 1},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[118.7, 31.9], [119.0, 31.9], [119.0, 32.2], [118.7, 32.2], [118.7, 31.9]]],
        },
    }],
}


class DataAcquisitionTests(unittest.TestCase):
    @patch.dict(os.environ, {"DATA_CACHE_ENABLED": "0"})
    @patch("geoai_agent.data_acquisition.request_json", return_value=BOUNDARY_RESPONSE)
    def test_boundary_download(self, mocked) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "boundary.gpkg"
            result = download_region_boundary({"REGION_NAME": "测试市", "OUTPUT": str(output)})
            self.assertEqual(result["feature_count"], 1)
            self.assertEqual(len(gpd.read_file(output)), 1)

    @patch.dict(os.environ, {"DATA_CACHE_ENABLED": "0"})
    def test_poi_and_road_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            boundary = Path(temp) / "boundary.gpkg"
            polygon = Polygon([(118.7, 31.9), (119.0, 31.9), (119.0, 32.2), (118.7, 32.2)])
            gpd.GeoDataFrame({"name": ["x"]}, geometry=[polygon], crs="EPSG:4326").to_file(boundary, driver="GPKG")
            poi_response = {"elements": [{"type": "node", "id": 1, "lat": 32.0, "lon": 118.8, "tags": {"name": "大学A", "amenity": "university"}}]}
            road_response = {"elements": [{"type": "way", "id": 2, "tags": {"highway": "primary"}, "geometry": [{"lat": 31.95, "lon": 118.75}, {"lat": 32.05, "lon": 118.85}]}]}
            with patch("geoai_agent.data_acquisition.request_json", return_value=poi_response):
                poi_path = Path(temp) / "pois.gpkg"
                self.assertEqual(download_osm_pois({"BOUNDARY": str(boundary), "POI_TYPE": "university", "OUTPUT": str(poi_path)})["feature_count"], 1)
            with patch("geoai_agent.data_acquisition.request_json", return_value=road_response):
                road_path = Path(temp) / "roads.gpkg"
                self.assertEqual(download_osm_roads({"AREA": str(boundary), "POINTS": str(poi_path), "REGION_NAME": "测试市", "POI_TYPE": "university", "DISTANCE": 500, "OUTPUT": str(road_path)})["feature_count"], 1)


if __name__ == "__main__":
    unittest.main()
