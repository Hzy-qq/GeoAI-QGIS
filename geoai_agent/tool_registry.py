from __future__ import annotations

from copy import deepcopy


def _tool(
    backend: str,
    algorithm: str,
    description: str,
    required: list[str],
    optional: list[str],
    properties: dict,
) -> dict:
    return {
        "backend": backend,
        "algorithm": algorithm,
        "description": description,
        "required_params": required,
        "optional_params": optional,
        "properties": properties,
    }


PATH = {"type": "string", "minLength": 1}
STRING = {"type": "string", "minLength": 1}
POSITIVE_INT = {"type": "integer", "minimum": 1}


TOOL_REGISTRY = {
    "load_neighbor_boundaries": _tool(
        "python", "python:load_neighbor_boundaries",
        "Load the bundled, versioned neighboring-city boundary fixture for topology analysis.",
        ["REGION_NAME", "OUTPUT"], [],
        {"REGION_NAME": STRING, "OUTPUT": PATH},
    ),
    "download_region_boundary": _tool(
        "python", "python:download_region_boundary",
        "Download one administrative boundary from the allowlisted Nominatim source.",
        ["REGION_NAME", "OUTPUT"], [],
        {"REGION_NAME": STRING, "OUTPUT": PATH},
    ),
    "download_osm_pois": _tool(
        "python", "python:download_osm_pois",
        "Download allowlisted OSM university/college POIs inside a boundary.",
        ["BOUNDARY", "POI_TYPE", "OUTPUT"], [],
        {"BOUNDARY": PATH, "POI_TYPE": {"type": "string", "enum": ["university"]}, "OUTPUT": PATH},
    ),
    "download_osm_roads": _tool(
        "python", "python:download_osm_roads",
        "Download allowlisted OSM highway ways around university POIs in an analysis area.",
        ["AREA", "POINTS", "REGION_NAME", "POI_TYPE", "DISTANCE", "OUTPUT"], [],
        {
            "AREA": PATH,
            "POINTS": PATH,
            "REGION_NAME": STRING,
            "POI_TYPE": {"type": "string", "enum": ["university"]},
            "DISTANCE": {"type": "number", "exclusiveMinimum": 0},
            "OUTPUT": PATH,
        },
    ),
    "validate_dataset": _tool(
        "python", "python:validate_dataset",
        "Validate feature count, CRS, geometry family and geometry validity.",
        ["INPUT", "GEOMETRY_TYPE"], ["MIN_FEATURES", "MAX_FEATURES"],
        {
            "INPUT": PATH,
            "GEOMETRY_TYPE": {"type": "string", "enum": ["point", "line", "polygon", "any"]},
            "MIN_FEATURES": {"type": "integer", "minimum": 0},
            "MAX_FEATURES": POSITIVE_INT,
        },
    ),
    "auto_reproject_layer": _tool(
        "python", "python:auto_reproject_layer",
        "Choose a local UTM CRS from the layer centroid and reproject the layer.",
        ["INPUT", "OUTPUT"], [], {"INPUT": PATH, "OUTPUT": PATH},
    ),
    "reproject_to_match": _tool(
        "python", "python:reproject_to_match",
        "Reproject a layer to exactly match a reference layer CRS.",
        ["INPUT", "REFERENCE", "OUTPUT"], [],
        {"INPUT": PATH, "REFERENCE": PATH, "OUTPUT": PATH},
    ),
    "buffer": _tool(
        "qgis", "native:buffer", "Create metric buffers around input features.",
        ["INPUT", "DISTANCE", "OUTPUT"], ["SEGMENTS", "DISSOLVE"],
        {
            "INPUT": PATH, "DISTANCE": {"type": "number", "exclusiveMinimum": 0},
            "OUTPUT": PATH, "SEGMENTS": POSITIVE_INT, "DISSOLVE": {"type": "boolean"},
        },
    ),
    "clip": _tool(
        "qgis", "native:clip", "Clip input features using an overlay layer.",
        ["INPUT", "OVERLAY", "OUTPUT"], [],
        {"INPUT": PATH, "OVERLAY": PATH, "OUTPUT": PATH},
    ),
    "sum_line_lengths": _tool(
        "qgis", "native:sumlinelengths", "Calculate total line length inside polygons.",
        ["POLYGONS", "LINES", "OUTPUT"], ["LEN_FIELD", "COUNT_FIELD"],
        {
            "POLYGONS": PATH, "LINES": PATH, "OUTPUT": PATH,
            "LEN_FIELD": STRING, "COUNT_FIELD": STRING,
        },
    ),
    "select_feature_by_attribute": _tool(
        "python", "python:select_feature_by_attribute",
        "Select features whose attribute matches a region name.",
        ["INPUT", "FIELD", "VALUE", "OUTPUT"], [],
        {"INPUT": PATH, "FIELD": STRING, "VALUE": STRING, "OUTPUT": PATH},
    ),
    "reproject_layer": _tool(
        "python", "python:reproject_layer", "Reproject a vector layer to a target CRS.",
        ["INPUT", "TARGET_CRS", "OUTPUT"], [],
        {"INPUT": PATH, "TARGET_CRS": STRING, "OUTPUT": PATH},
    ),
    "calculate_polygon_area": _tool(
        "python", "python:calculate_polygon_area", "Calculate polygon area in square kilometres.",
        ["INPUT", "AREA_FIELD", "OUTPUT"], [],
        {"INPUT": PATH, "AREA_FIELD": STRING, "OUTPUT": PATH},
    ),
    "find_adjacent_polygons": _tool(
        "python", "python:find_adjacent_polygons", "Find polygons that touch a target polygon.",
        ["INPUT", "TARGET", "NAME_FIELD", "OUTPUT"], ["TOLERANCE_M"],
        {
            "INPUT": PATH, "TARGET": PATH, "NAME_FIELD": STRING, "OUTPUT": PATH,
            "TOLERANCE_M": {"type": "number", "minimum": 0},
        },
    ),
    "count_points_in_polygon": _tool(
        "python", "python:count_points_in_polygon", "Count point features inside polygons.",
        ["POLYGONS", "POINTS", "COUNT_FIELD", "OUTPUT"], [],
        {"POLYGONS": PATH, "POINTS": PATH, "COUNT_FIELD": STRING, "OUTPUT": PATH},
    ),
}


def get_tool_config(tool_name: str) -> dict:
    if tool_name not in TOOL_REGISTRY:
        raise ValueError(
            f"Unknown tool: {tool_name}. Available tools: {', '.join(TOOL_REGISTRY)}"
        )
    return TOOL_REGISTRY[tool_name]


def tool_to_function_schema(tool_name: str) -> dict:
    config = get_tool_config(tool_name)
    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": config["description"],
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": deepcopy(config["properties"]),
                "required": list(config["required_params"]),
            },
        },
    }


def tool_registry_to_function_schemas(tool_names: list[str] | None = None) -> list[dict]:
    names = tool_names or list(TOOL_REGISTRY)
    return [tool_to_function_schema(name) for name in names]
