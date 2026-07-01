from __future__ import annotations

import os
import sys
from pathlib import Path


def configure_gdal_runtime() -> dict[str, str]:
    candidates = [
        Path(sys.prefix) / "Library" / "share" / "gdal",
        Path(r"F:\QGIS\apps\gdal\share\gdal"),
    ]
    configured: dict[str, str] = {}
    if not os.getenv("GDAL_DATA"):
        gdal_data = next((path for path in candidates if path.is_dir()), None)
        if gdal_data:
            os.environ["GDAL_DATA"] = str(gdal_data)
            configured["GDAL_DATA"] = str(gdal_data)
    if not os.getenv("OSM_CONFIG_FILE"):
        osm_config = next(
            (path / "osmconf.ini" for path in candidates if (path / "osmconf.ini").exists()),
            None,
        )
        if osm_config:
            os.environ["OSM_CONFIG_FILE"] = str(osm_config)
            configured["OSM_CONFIG_FILE"] = str(osm_config)
    return configured
