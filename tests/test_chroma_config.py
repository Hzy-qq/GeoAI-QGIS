from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from geoai_agent.chroma_store import get_chroma_collection_name, get_chroma_path


class ChromaConfigTests(unittest.TestCase):
    def test_relative_env_path_is_resolved_from_project_root(self) -> None:
        with patch.dict(
            os.environ,
            {"CHROMA_PATH": "outputs/test_chroma", "CHROMA_COLLECTION": "test_collection"},
        ):
            self.assertTrue(get_chroma_path().is_absolute())
            self.assertEqual(get_chroma_path().parts[-2:], ("outputs", "test_chroma"))
            self.assertEqual(get_chroma_collection_name(), "test_collection")


if __name__ == "__main__":
    unittest.main()
