from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


try:
    from backend.database import Database
    from backend.service import TaskService
except ImportError:  # pragma: no cover
    Database = None
    TaskService = None


@unittest.skipIf(Database is None, "SQLAlchemy backend dependencies are not installed")
class TaskServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        path = Path(self.temp_dir.name) / "service.db"
        self.database = Database(f"sqlite:///{path.as_posix()}")
        self.database.create_schema()
        self.service = TaskService(self.database)

    def tearDown(self) -> None:
        self.database.dispose()
        self.temp_dir.cleanup()

    def test_worker_claim_changes_status(self) -> None:
        created = self.service.create_task("南京市面积是多少", "test-user", "claim-key")
        claimed = self.service.claim_next("worker-test")
        self.assertEqual(claimed["task_id"], created["task_id"])
        self.assertEqual(self.service.get_task(created["task_id"])["status"], "RUNNING")
        self.assertTrue(created["conversation_id"])

    def test_unknown_task_raises(self) -> None:
        with self.assertRaises(LookupError):
            self.service.get_task("missing")


if __name__ == "__main__":
    unittest.main()
