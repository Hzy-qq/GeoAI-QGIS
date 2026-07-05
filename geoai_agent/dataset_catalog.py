from __future__ import annotations

from copy import deepcopy
from urllib.parse import urlparse

from .errors import PermanentError


DATASET_CATALOG = {
    "neighbor_boundaries": {
        "source_id": "bundled_gadm_4_1_fixture",
        "endpoint": "data/fixtures/nanjing_neighbor_cities.gpkg",
        "license": "GADM 4.1 academic/non-commercial terms; bundled test fixture",
        "geometry": "polygon",
    },
    "administrative_boundary": {
        "source_id": "osm_nominatim",
        "endpoint": "https://nominatim.openstreetmap.org/search",
        "license": "OpenStreetMap contributors, ODbL 1.0",
        "geometry": "polygon",
    },
    "university_pois": {
        "source_id": "osm_overpass",
        "endpoint": "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
        "fallback_endpoints": [
            "https://overpass.private.coffee/api/interpreter",
            "https://overpass-api.de/api/interpreter"
        ],
        "license": "OpenStreetMap contributors, ODbL 1.0",
        "geometry": "point",
        "osm_filter": '["amenity"~"^(university|college)$"]',
    },
    "road_network": {
        "source_id": "osm_overpass",
        "endpoint": "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
        "fallback_endpoints": [
            "https://overpass.private.coffee/api/interpreter",
            "https://overpass-api.de/api/interpreter"
        ],
        "license": "OpenStreetMap contributors, ODbL 1.0",
        "geometry": "line",
        "osm_filter": '["highway"~"^(motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link|tertiary|tertiary_link|unclassified|residential|living_street)$"]',
    },
    "road_network_jiangsu_extract": {
        "source_id": "geofabrik",
        "endpoint": "https://download.geofabrik.de/asia/china/jiangsu-latest.osm.pbf",
        "license": "OpenStreetMap contributors, ODbL 1.0; extract by Geofabrik",
        "geometry": "line",
    },
}

ALLOWED_HOSTS = {
    "nominatim.openstreetmap.org",
    "overpass-api.de",
    "overpass.private.coffee",
    "maps.mail.ru",
    "download.geofabrik.de",
}


def get_dataset_spec(dataset_id: str) -> dict:
    try:
        return deepcopy(DATASET_CATALOG[dataset_id])
    except KeyError as exc:
        raise PermanentError(f"Unknown dataset_id: {dataset_id}") from exc


def validate_catalog_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise PermanentError(f"Blocked data source URL: {url}")
