from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon

from geoai_agent.data_acquisition import (
    _request_overpass,
    _poi_tile_batch_is_usable,
    _should_try_geofabrik,
    _vector_tile_batch_is_usable,
    _vector_tiles_for_area,
    download_osm_pois,
    download_osm_roads,
    download_osm_roads_in_area,
    download_osm_water,
    download_region_boundary,
)
from geoai_agent.errors import TransientError


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
    @patch.dict(os.environ, {"POI_TILE_MIN_SUCCESS_RATIO": "0.75"})
    def test_poi_tile_batch_accepts_bounded_partial_coverage(self) -> None:
        self.assertTrue(_poi_tile_batch_is_usable(3, 4))
        self.assertFalse(_poi_tile_batch_is_usable(2, 4))

    @patch.dict(
        os.environ,
        {
            "DATA_CACHE_ENABLED": "0",
            "POI_SOURCE_MODE": "overpass",
            "POI_TILE_WORKERS": "1",
            "POI_TILE_MIN_SUCCESS_RATIO": "0.75",
        },
    )
    def test_poi_tiles_keep_successful_batches_when_one_of_four_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            boundary_path = root / "boundary.gpkg"
            output_path = root / "pois.gpkg"
            polygon = Polygon(
                [(118.7, 31.9), (119.0, 31.9), (119.0, 32.2), (118.7, 32.2)]
            )
            gpd.GeoDataFrame(
                {"name": ["area"]}, geometry=[polygon], crs="EPSG:4326"
            ).to_file(boundary_path, driver="GPKG")
            response = {
                "elements": [{
                    "type": "node", "id": 1, "lat": 32.0, "lon": 118.8,
                    "tags": {"name": "Hospital A", "amenity": "hospital"},
                }]
            }
            tiles = [
                (31.9, 118.7, 32.0, 118.8),
                (31.9, 118.8, 32.0, 118.9),
                (32.0, 118.7, 32.1, 118.8),
                (32.0, 118.8, 32.1, 118.9),
            ]
            with (
                patch("geoai_agent.data_acquisition._degree_tiles_for_area", return_value=tiles),
                patch(
                    "geoai_agent.data_acquisition._request_overpass",
                    side_effect=[response, response, response, TransientError("timeout")],
                ),
            ):
                result = download_osm_pois({
                    "BOUNDARY": str(boundary_path),
                    "POI_TYPE": "hospital",
                    "OUTPUT": str(output_path),
                })
            self.assertTrue(result["partial_tiles"])
            self.assertEqual(result["tile_count_downloaded"], 3)
            self.assertEqual(result["tile_count_failed"], 1)
            self.assertEqual(result["feature_count"], 1)
            layer = gpd.read_file(output_path)
            self.assertEqual(layer.iloc[0]["tile_download_status"], "partial")

    @patch.dict(
        os.environ,
        {
            "DATA_CACHE_ENABLED": "0", "POI_SOURCE_MODE": "overpass",
            "POI_TILE_WORKERS": "1", "POI_TILE_DEGREES": "2",
        },
    )
    def test_subway_station_query_excludes_entrances_and_non_node_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            boundary_path = root / "boundary.gpkg"
            output_path = root / "subway.gpkg"
            polygon = Polygon(
                [(118.7, 31.9), (119.0, 31.9), (119.0, 32.2), (118.7, 32.2)]
            )
            gpd.GeoDataFrame(
                {"name": ["area"]}, geometry=[polygon], crs="EPSG:4326"
            ).to_file(boundary_path, driver="GPKG")
            response = {
                "elements": [{
                    "type": "node", "id": 2, "lat": 32.0, "lon": 118.8,
                    "tags": {"name": "Station A", "railway": "station", "station": "subway"},
                }]
            }
            with patch(
                "geoai_agent.data_acquisition._request_overpass", return_value=response
            ) as requested:
                result = download_osm_pois({
                    "BOUNDARY": str(boundary_path),
                    "POI_TYPE": "subway_station",
                    "OUTPUT": str(output_path),
                })
            query = requested.call_args.args[1]
            self.assertIn('node["railway"="station"]', query)
            self.assertNotIn("subway_entrance", query)
            self.assertNotIn("  way", query)
            self.assertEqual(result["feature_count"], 1)

    @patch.dict(os.environ, {"ROAD_VECTOR_TILE_MIN_SUCCESS_RATIO": "0.75"})
    def test_vector_tile_batch_accepts_one_failure_out_of_sixteen(self) -> None:
        self.assertTrue(_vector_tile_batch_is_usable(15, 16))
        self.assertTrue(_vector_tile_batch_is_usable(12, 16))
        self.assertFalse(_vector_tile_batch_is_usable(11, 16))
        self.assertFalse(_vector_tile_batch_is_usable(0, 16))

    def test_road_overpass_request_limits_endpoint_attempts_and_timeout(self) -> None:
        spec = {
            "endpoint": "https://overpass-api.de/api/interpreter",
            "fallback_endpoints": [
                "https://overpass.private.coffee/api/interpreter",
                "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
            ],
        }
        with patch(
            "geoai_agent.data_acquisition.request_json",
            side_effect=TransientError("timed out"),
        ) as mocked:
            with self.assertRaises(TransientError):
                _request_overpass(
                    spec,
                    "road query",
                    start_index=1,
                    timeout_seconds=7,
                    max_endpoints=2,
                )
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(mocked.call_args_list[0].args[0], spec["endpoint"])
        self.assertEqual(mocked.call_args_list[1].args[0], spec["fallback_endpoints"][1])
        self.assertTrue(all(call.kwargs["timeout"] == 7 for call in mocked.call_args_list))

    def test_auto_road_source_does_not_download_missing_province_extract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            extract = Path(temp) / "jiangsu-latest.osm.pbf"
            with (
                patch.dict(os.environ, {"DATA_REFRESH_EXTRACTS": "0"}),
                patch(
                    "geoai_agent.data_acquisition._geofabrik_extract_path",
                    return_value=extract,
                ),
            ):
                self.assertFalse(_should_try_geofabrik("auto"))
                self.assertTrue(_should_try_geofabrik("geofabrik"))
                extract.touch()
                self.assertTrue(_should_try_geofabrik("auto"))

    @patch.dict(
        os.environ,
        {
            "DATA_CACHE_ENABLED": "0",
            "ROAD_SOURCE_MODE": "overpass",
            "ROAD_SPLIT_RETRY_DEPTH": "1",
            "MAIN_ROAD_TILE_DEGREES": "0.2",
        },
    )
    def test_failed_road_bbox_is_split_into_four_smaller_retries(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            area_path = root / "area.gpkg"
            points_path = root / "points.gpkg"
            output_path = root / "roads.gpkg"
            polygon = Polygon(
                [(118.7, 31.9), (119.0, 31.9), (119.0, 32.2), (118.7, 32.2)]
            )
            gpd.GeoDataFrame(
                {"name": ["area"]}, geometry=[polygon], crs="EPSG:4326"
            ).to_file(area_path, driver="GPKG")
            gpd.GeoDataFrame(
                {"name": ["poi"]}, geometry=[Point(118.8, 32.0)], crs="EPSG:4326"
            ).to_file(points_path, driver="GPKG")
            road_response = {
                "elements": [{
                    "type": "way", "id": 42,
                    "tags": {"highway": "primary"},
                    "geometry": [
                        {"lat": 31.95, "lon": 118.75},
                        {"lat": 32.05, "lon": 118.85},
                    ],
                }]
            }
            with patch(
                "geoai_agent.data_acquisition._request_overpass",
                side_effect=[TransientError("HTTP 504"), *([road_response] * 4)],
            ) as requested:
                metrics = download_osm_roads({
                    "AREA": str(area_path),
                    "POINTS": str(points_path),
                    "REGION_NAME": "测试市",
                    "POI_TYPE": "university",
                    "DISTANCE": 1000,
                    "ROAD_LEVEL": "main",
                    "OUTPUT": str(output_path),
                })
            self.assertEqual(requested.call_count, 5)
            self.assertEqual(metrics["feature_count"], 1)
            self.assertEqual(metrics["road_level"], "main")

    @patch.dict(os.environ, {"DATA_CACHE_ENABLED": "0"})
    @patch("geoai_agent.data_acquisition.request_json", return_value=BOUNDARY_RESPONSE)
    def test_boundary_download(self, mocked) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "boundary.gpkg"
            result = download_region_boundary({"REGION_NAME": "测试市", "OUTPUT": str(output)})
            self.assertEqual(result["feature_count"], 1)
            self.assertEqual(len(gpd.read_file(output)), 1)

    @patch.dict(
        os.environ,
        {
            "DATA_CACHE_ENABLED": "0",
            "POI_SOURCE_MODE": "auto",
            "ROAD_SOURCE_MODE": "auto",
            "WATER_SOURCE_MODE": "auto",
        },
    )
    def test_normalized_offline_pack_avoids_all_network_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            polygon = Polygon(
                [(118.7, 31.9), (119.0, 31.9), (119.0, 32.2), (118.7, 32.2)]
            )
            boundary = root / "boundary.gpkg"
            gpd.GeoDataFrame(
                {"name": ["南京市"]}, geometry=[polygon], crs="EPSG:4326"
            ).to_file(boundary, driver="GPKG")
            poi = root / "poi_hospital.gpkg"
            gpd.GeoDataFrame(
                {
                    "name": ["Hospital A"],
                    "poi_type": ["hospital"],
                    "data_source": ["osm_local_pbf_snapshot"],
                },
                geometry=[Point(118.8, 32.0)],
                crs="EPSG:4326",
            ).to_file(poi, driver="GPKG")
            roads = root / "main_roads.gpkg"
            gpd.GeoDataFrame(
                {
                    "name": ["Road A"],
                    "highway": ["primary"],
                    "data_source": ["osm_local_pbf_snapshot"],
                },
                geometry=[LineString([(118.75, 31.95), (118.85, 32.05)])],
                crs="EPSG:4326",
            ).to_file(roads, driver="GPKG")
            water = root / "water.gpkg"
            gpd.GeoDataFrame(
                {"name": ["Lake A"], "data_source": ["osm_local_pbf_snapshot"]},
                geometry=[Polygon([
                    (118.78, 31.98), (118.82, 31.98), (118.82, 32.02),
                    (118.78, 32.02), (118.78, 31.98),
                ])],
                crs="EPSG:4326",
            ).to_file(water, driver="GPKG")
            extract = root / "jiangsu-latest.osm.pbf"
            extract.write_bytes(b"offline-snapshot")
            packs = {
                "boundary": boundary,
                "poi_hospital": poi,
                "main_roads": roads,
                "water": water,
            }

            with (
                patch(
                    "geoai_agent.data_acquisition._offline_pack_path",
                    side_effect=lambda name: packs[name],
                ),
                patch(
                    "geoai_agent.data_acquisition._geofabrik_extract_path",
                    return_value=extract,
                ),
                patch("geoai_agent.data_acquisition._request_overpass") as overpass,
                patch("geoai_agent.data_acquisition.request_json") as http_json,
            ):
                poi_result = download_osm_pois({
                    "BOUNDARY": str(boundary), "POI_TYPE": "hospital",
                    "OUTPUT": str(root / "poi_output.gpkg"),
                })
                road_result = download_osm_roads_in_area({
                    "AREA": str(boundary), "REGION_NAME": "南京市",
                    "ROAD_LEVEL": "main", "OUTPUT": str(root / "road_output.gpkg"),
                })
                water_result = download_osm_water({
                    "BOUNDARY": str(boundary), "OUTPUT": str(root / "water_output.gpkg"),
                })

            self.assertTrue(poi_result["offline_pack_hit"])
            self.assertTrue(road_result["offline_pack_hit"])
            self.assertTrue(water_result["offline_pack_hit"])
            overpass.assert_not_called()
            http_json.assert_not_called()

    @patch.dict(
        os.environ,
        {
            "DATA_CACHE_ENABLED": "0", "ROAD_SOURCE_MODE": "overpass",
            "POI_SOURCE_MODE": "overpass",
        },
    )
    def test_poi_and_road_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            boundary = Path(temp) / "boundary.gpkg"
            polygon = Polygon([(118.7, 31.9), (119.0, 31.9), (119.0, 32.2), (118.7, 32.2)])
            gpd.GeoDataFrame({"name": ["x"]}, geometry=[polygon], crs="EPSG:4326").to_file(boundary, driver="GPKG")
            poi_response = {"elements": [{"type": "node", "id": 1, "lat": 32.0, "lon": 118.8, "tags": {"name": "大学A", "amenity": "university"}}]}
            road_response = {"elements": [
                {"type": "way", "id": 2, "tags": {"highway": "primary"}, "geometry": [{"lat": 31.95, "lon": 118.75}, {"lat": 32.05, "lon": 118.85}]},
                {"type": "way", "id": 3, "tags": {"highway": "residential"}, "geometry": [{"lat": 31.96, "lon": 118.76}, {"lat": 32.04, "lon": 118.84}]},
            ]}
            with patch("geoai_agent.data_acquisition.request_json", return_value=poi_response):
                poi_path = Path(temp) / "pois.gpkg"
                self.assertEqual(download_osm_pois({"BOUNDARY": str(boundary), "POI_TYPE": "university", "OUTPUT": str(poi_path)})["feature_count"], 1)
            with patch("geoai_agent.data_acquisition.request_json", return_value=road_response) as road_request:
                road_path = Path(temp) / "roads.gpkg"
                metrics = download_osm_roads({"AREA": str(boundary), "POINTS": str(poi_path), "REGION_NAME": "测试市", "POI_TYPE": "university", "DISTANCE": 500, "ROAD_LEVEL": "main", "OUTPUT": str(road_path)})
                self.assertEqual(metrics["feature_count"], 1)
                self.assertEqual(metrics["road_level"], "main")
                self.assertEqual(gpd.read_file(road_path).iloc[0]["highway"], "primary")
                query = road_request.call_args.kwargs["form"]["data"]
                self.assertIn("secondary", query)
                self.assertNotIn("residential", query)

    def test_vector_tile_selection_is_bounded_to_intersecting_tiles(self) -> None:
        area = gpd.GeoDataFrame(
            {"name": ["small"]},
            geometry=[Polygon([
                (118.79, 31.99), (118.81, 31.99),
                (118.81, 32.01), (118.79, 32.01), (118.79, 31.99),
            ])],
            crs="EPSG:4326",
        )
        tiles = _vector_tiles_for_area(area, 11)
        self.assertGreaterEqual(len(tiles), 1)
        self.assertLessEqual(len(tiles), 4)

    @patch.dict(
        os.environ,
        {"DATA_CACHE_ENABLED": "0", "ROAD_SOURCE_MODE": "auto"},
    )
    def test_auto_road_source_prefers_vector_tiles_without_calling_overpass(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            area_path = root / "area.gpkg"
            points_path = root / "points.gpkg"
            output_path = root / "roads.gpkg"
            polygon = Polygon(
                [(118.7, 31.9), (119.0, 31.9), (119.0, 32.2), (118.7, 32.2)]
            )
            gpd.GeoDataFrame(
                {"name": ["area"]}, geometry=[polygon], crs="EPSG:4326"
            ).to_file(area_path, driver="GPKG")
            gpd.GeoDataFrame(
                {"name": ["poi"]}, geometry=[Point(118.8, 32.0)], crs="EPSG:4326"
            ).to_file(points_path, driver="GPKG")
            expected = {
                "output": str(output_path),
                "feature_count": 3,
                "cache_hit": False,
                "road_level": "main",
                "road_source": "osm_shortbread_vector_tiles",
            }
            with (
                patch(
                    "geoai_agent.data_acquisition._download_roads_from_vector_tiles",
                    return_value=expected,
                ) as vector_download,
                patch("geoai_agent.data_acquisition._request_overpass") as overpass,
            ):
                result = download_osm_roads({
                    "AREA": str(area_path),
                    "POINTS": str(points_path),
                    "REGION_NAME": "测试市",
                    "POI_TYPE": "university",
                    "DISTANCE": 1000,
                    "ROAD_LEVEL": "main",
                    "OUTPUT": str(output_path),
                })
            self.assertEqual(result["road_source"], "osm_shortbread_vector_tiles")
            vector_download.assert_called_once()
            overpass.assert_not_called()

    @patch.dict(
        os.environ,
        {
            "DATA_CACHE_ENABLED": "0", "ROAD_SOURCE_MODE": "overpass",
            "POI_SOURCE_MODE": "overpass",
        },
    )
    def test_generic_poi_area_roads_and_water_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            boundary = root / "boundary.gpkg"
            polygon = Polygon(
                [(118.7, 31.9), (119.0, 31.9), (119.0, 32.2), (118.7, 32.2)]
            )
            gpd.GeoDataFrame(
                {"name": ["x"]}, geometry=[polygon], crs="EPSG:4326"
            ).to_file(boundary, driver="GPKG")

            hospital_response = {
                "elements": [{
                    "type": "node", "id": 11, "lat": 32.0, "lon": 118.8,
                    "tags": {"name": "Hospital A", "amenity": "hospital"},
                }]
            }
            with patch(
                "geoai_agent.data_acquisition.request_json",
                return_value=hospital_response,
            ):
                hospital_path = root / "hospitals.gpkg"
                metrics = download_osm_pois({
                    "BOUNDARY": str(boundary), "POI_TYPE": "hospital",
                    "OUTPUT": str(hospital_path),
                })
                self.assertEqual(metrics["feature_count"], 1)
                self.assertEqual(gpd.read_file(hospital_path).iloc[0]["poi_type"], "hospital")

            road_response = {
                "elements": [{
                    "type": "way", "id": 12, "tags": {"highway": "primary"},
                    "geometry": [
                        {"lat": 31.95, "lon": 118.75},
                        {"lat": 32.05, "lon": 118.85},
                    ],
                }]
            }
            with patch(
                "geoai_agent.data_acquisition.request_json", return_value=road_response,
            ):
                road_path = root / "area_roads.gpkg"
                metrics = download_osm_roads_in_area({
                    "AREA": str(boundary), "REGION_NAME": "测试市",
                    "ROAD_LEVEL": "main", "OUTPUT": str(road_path),
                })
                self.assertEqual(metrics["feature_count"], 1)

            water_response = {
                "elements": [{
                    "type": "way", "id": 13,
                    "tags": {"natural": "water", "name": "Lake A"},
                    "geometry": [
                        {"lat": 31.98, "lon": 118.78},
                        {"lat": 31.98, "lon": 118.82},
                        {"lat": 32.02, "lon": 118.82},
                        {"lat": 32.02, "lon": 118.78},
                        {"lat": 31.98, "lon": 118.78},
                    ],
                }]
            }
            with patch(
                "geoai_agent.data_acquisition.request_json", return_value=water_response,
            ):
                water_path = root / "water.gpkg"
                metrics = download_osm_water({
                    "BOUNDARY": str(boundary), "OUTPUT": str(water_path),
                })
                self.assertEqual(metrics["feature_count"], 1)


if __name__ == "__main__":
    unittest.main()
