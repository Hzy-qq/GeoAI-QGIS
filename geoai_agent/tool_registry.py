TOOL_REGISTRY = {
    "buffer": {
        "algorithm": "native:buffer",
        "description": "Create buffer polygons around input features.",
        "required_params": ["INPUT", "DISTANCE", "OUTPUT"],
        "optional_params": ["SEGMENTS", "DISSOLVE"],
    },
    "clip": {
        "algorithm": "native:clip",
        "description": "Clip input features using an overlay layer.",
        "required_params": ["INPUT", "OVERLAY", "OUTPUT"],
        "optional_params": [],
    },
    "sum_line_lengths": {
        "algorithm": "native:sumlinelengths",
        "description": "Calculate total line length inside polygons.",
        "required_params": ["POLYGONS", "LINES", "OUTPUT"],
        "optional_params": ["LEN_FIELD", "COUNT_FIELD"],
    },
}


def get_tool_config(tool_name: str) -> dict:
    if tool_name not in TOOL_REGISTRY:
        available_tools = ", ".join(TOOL_REGISTRY.keys())
        raise ValueError(
            f"Unknown tool: {tool_name}. Available tools: {available_tools}"
        )

    return TOOL_REGISTRY[tool_name]
