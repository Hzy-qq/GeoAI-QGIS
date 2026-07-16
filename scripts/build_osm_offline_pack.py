from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SCRIPT_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_PROJECT_ROOT))

from geoai_agent.config import PROJECT_ROOT
from geoai_agent.data_acquisition import (
    download_osm_pois,
    download_osm_roads_in_area,
    download_osm_water,
    download_region_boundary,
)
from geoai_agent.dataset_catalog import SUPPORTED_POI_TYPES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build normalized Nanjing GIS layers from the bundled Jiangsu OSM PBF."
    )
    parser.add_argument(
        "--poi-types",
        nargs="*",
        choices=SUPPORTED_POI_TYPES,
        default=list(SUPPORTED_POI_TYPES),
        help="POI layers to rebuild. Defaults to every supported POI type.",
    )
    parser.add_argument(
        "--skip-base",
        action="store_true",
        help="Keep existing boundary, roads and water layers and only rebuild selected POIs.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.environ["DATA_CACHE_ENABLED"] = "0"
    os.environ["BOUNDARY_SOURCE_MODE"] = "local_pbf"
    os.environ["POI_SOURCE_MODE"] = "local_pbf"
    os.environ["WATER_SOURCE_MODE"] = "local_pbf"
    os.environ["ROAD_SOURCE_MODE"] = "auto"
    os.environ["OSM_OFFLINE_PACK_BUILDING"] = "1"

    pack_root = PROJECT_ROOT / "data" / "osm" / "nanjing"
    pack_root.mkdir(parents=True, exist_ok=True)
    boundary = pack_root / "boundary.gpkg"
    if not args.skip_base or not boundary.exists():
        metrics = download_region_boundary(
            {"REGION_NAME": "南京市", "OUTPUT": str(boundary)}
        )
        print(f"boundary: {metrics['feature_count']} features")

    for poi_type in args.poi_types:
        output = pack_root / f"poi_{poi_type}.gpkg"
        metrics = download_osm_pois(
            {
                "BOUNDARY": str(boundary),
                "POI_TYPE": poi_type,
                "OUTPUT": str(output),
            }
        )
        print(f"poi/{poi_type}: {metrics['feature_count']} features")

    if not args.skip_base:
        roads = download_osm_roads_in_area(
            {
                "AREA": str(boundary),
                "REGION_NAME": "南京市",
                "ROAD_LEVEL": "main",
                "OUTPUT": str(pack_root / "main_roads.gpkg"),
            }
        )
        print(f"main_roads: {roads['feature_count']} features")
        water = download_osm_water(
            {"BOUNDARY": str(boundary), "OUTPUT": str(pack_root / "water.gpkg")}
        )
        print(f"water: {water['feature_count']} features")


if __name__ == "__main__":
    main()
