from __future__ import annotations

import unittest

from geoai_agent.tool_registry import TOOL_REGISTRY, tool_registry_to_function_schemas


class ToolRegistryTests(unittest.TestCase):
    def test_dynamic_tools_exist(self) -> None:
        required = {
            "download_region_boundary", "download_osm_pois", "download_osm_roads",
            "validate_dataset", "auto_reproject_layer", "reproject_to_match",
            "buffer", "clip", "sum_line_lengths",
        }
        self.assertTrue(required.issubset(TOOL_REGISTRY))

    def test_every_tool_converts_to_function_schema(self) -> None:
        schemas = tool_registry_to_function_schemas()
        self.assertEqual(len(schemas), len(TOOL_REGISTRY))
        for schema in schemas:
            function = schema["function"]
            self.assertFalse(function["parameters"]["additionalProperties"])
            self.assertIn("required", function["parameters"])


if __name__ == "__main__":
    unittest.main()
