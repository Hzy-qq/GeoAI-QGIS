from __future__ import annotations

import unittest
from unittest.mock import patch

from geoai_agent.errors import TransientError
from geoai_agent.python_gis_tools import run_python_tool


class ErrorClassificationTests(unittest.TestCase):
    def test_transient_download_error_survives_python_tool_wrapper(self) -> None:
        with patch(
            "geoai_agent.python_gis_tools.PYTHON_TOOL_HANDLERS",
            {"download_region_boundary": lambda params: (_ for _ in ()).throw(TransientError("timeout"))},
        ):
            result = run_python_tool(
                "download_region_boundary",
                {"REGION_NAME": "南京市", "OUTPUT": "unused.gpkg"},
            )
        self.assertFalse(result["success"])
        self.assertEqual(result["error_type"], "transient")


if __name__ == "__main__":
    unittest.main()
