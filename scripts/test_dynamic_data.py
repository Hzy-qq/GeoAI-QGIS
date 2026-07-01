from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geoai_agent.data_acquisition import download_osm_pois, download_region_boundary
from geoai_agent.data_validation import validate_dataset
from geoai_agent.task_workspace import TaskWorkspace


def main() -> None:
    parser = argparse.ArgumentParser(description="Test live boundary and POI downloads without LLM/QGIS.")
    parser.add_argument("region", nargs="?", default="南京市")
    args = parser.parse_args()
    workspace = TaskWorkspace.create()
    boundary = workspace.resolve("workspace://raw/region_boundary.gpkg")
    pois = workspace.resolve("workspace://raw/university_pois.gpkg")
    print(download_region_boundary({"REGION_NAME": args.region, "OUTPUT": str(boundary)}))
    print(validate_dataset({"INPUT": str(boundary), "GEOMETRY_TYPE": "polygon"}))
    print(download_osm_pois({
        "BOUNDARY": str(boundary),
        "POI_TYPE": "university",
        "OUTPUT": str(pois),
    }))
    print(validate_dataset({"INPUT": str(pois), "GEOMETRY_TYPE": "point"}))
    print("Task workspace:", workspace.root)


if __name__ == "__main__":
    main()
