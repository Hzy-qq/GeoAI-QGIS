from __future__ import annotations

from copy import deepcopy
from urllib.parse import urlparse

from .errors import PermanentError


POI_FILTERS = {
    "university": ['["amenity"~"^(university|college)$"]'],
    "school": ['["amenity"="school"]'],
    "hospital": ['["amenity"="hospital"]'],
    "clinic": ['["amenity"~"^(clinic|doctors)$"]'],
    "pharmacy": ['["amenity"="pharmacy"]'],
    "subway_station": [
        '["railway"="station"]["station"="subway"]',
        '["public_transport"="station"]["subway"="yes"]',
    ],
    "park": ['["leisure"="park"]'],
    "police": ['["amenity"="police"]'],
    "fire_station": ['["amenity"="fire_station"]'],
    "supermarket": ['["shop"="supermarket"]'],
    "charging_station": ['["amenity"="charging_station"]'],
}

POI_LABELS = {
    "university": "高校",
    "school": "学校",
    "hospital": "医院",
    "clinic": "诊所",
    "pharmacy": "药店",
    "subway_station": "地铁站",
    "park": "公园",
    "police": "公安机构",
    "fire_station": "消防站",
    "supermarket": "超市",
    "charging_station": "充电站",
}

SUPPORTED_POI_TYPES = tuple(POI_FILTERS)


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
    "osm_pois": {
        "source_id": "osm_overpass",
        "endpoint": "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
        "fallback_endpoints": [
            "https://overpass.private.coffee/api/interpreter",
            "https://overpass-api.de/api/interpreter",
        ],
        "license": "OpenStreetMap contributors, ODbL 1.0",
        "geometry": "point",
    },
    "water_areas": {
        "source_id": "osm_overpass",
        "endpoint": "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
        "fallback_endpoints": [
            "https://overpass.private.coffee/api/interpreter",
            "https://overpass-api.de/api/interpreter",
        ],
        "license": "OpenStreetMap contributors, ODbL 1.0",
        "geometry": "polygon",
    },
    "road_network": {
        "source_id": "osm_overpass",
        "endpoint": "https://overpass-api.de/api/interpreter",
        "fallback_endpoints": [
            "https://overpass.private.coffee/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter"
        ],
        "license": "OpenStreetMap contributors, ODbL 1.0",
        "geometry": "line",
        "osm_filter": '["highway"~"^(motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link|tertiary|tertiary_link|unclassified|residential|living_street)$"]',
    },
    "road_vector_tiles": {
        "source_id": "osm_shortbread_vector_tiles",
        "endpoint": "https://vector.openstreetmap.org/shortbread_v1/{z}/{x}/{y}.mvt",
        "license": "OpenStreetMap contributors, ODbL 1.0; Shortbread vector tiles",
        "geometry": "line",
    },
    "water_vector_tiles": {
        "source_id": "osm_shortbread_vector_tiles",
        "endpoint": "https://vector.openstreetmap.org/shortbread_v1/{z}/{x}/{y}.mvt",
        "license": "OpenStreetMap contributors, ODbL 1.0; Shortbread vector tiles",
        "geometry": "polygon",
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
    "vector.openstreetmap.org",
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
