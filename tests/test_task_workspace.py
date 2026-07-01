from __future__ import annotations

import unittest

from geoai_agent.errors import PermanentError
from geoai_agent.task_workspace import TaskWorkspace


class TaskWorkspaceTests(unittest.TestCase):
    def test_resolves_task_uri(self) -> None:
        workspace = TaskWorkspace.create("unit_workspace")
        path = workspace.resolve("workspace://raw/input.gpkg")
        self.assertEqual(path, workspace.root / "raw" / "input.gpkg")

    def test_rejects_unsafe_task_id(self) -> None:
        with self.assertRaises(PermanentError):
            TaskWorkspace.create("../escape")

    def test_rejects_path_escape(self) -> None:
        workspace = TaskWorkspace.create("unit_escape")
        with self.assertRaises(PermanentError):
            workspace.resolve("workspace://../../../../outside.txt")


if __name__ == "__main__":
    unittest.main()
