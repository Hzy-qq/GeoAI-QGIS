from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path


try:
    from backend.database import Database
    from backend.models import AgentTask, utc_now
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

    def test_pending_tasks_expose_queue_position(self) -> None:
        first = self.service.create_task("南京市面积是多少", "test-user", "queue-key-1")
        second = self.service.create_task("统计南京市医院数量", "test-user", "queue-key-2")
        self.assertEqual(first["queue_position"], 1)
        self.assertEqual(second["queue_position"], 2)
        self.service.claim_next("worker-test")
        self.assertEqual(self.service.get_task(second["task_id"])["queue_position"], 1)

    def test_stale_pending_tasks_expire_before_replay(self) -> None:
        created = self.service.create_task("南京市面积是多少", "test-user", "expired-key")
        with self.database.session() as session:
            task = session.get(AgentTask, created["task_id"])
            task.created_at = utc_now() - timedelta(seconds=60)
            session.commit()
        self.assertEqual(self.service.expire_pending(30), 1)
        task = self.service.get_task(created["task_id"])
        self.assertEqual(task["status"], "FAILED")
        self.assertEqual(task["error_code"], "QUEUE_EXPIRED")

    def test_unknown_task_raises(self) -> None:
        with self.assertRaises(LookupError):
            self.service.get_task("missing")

    def test_pending_task_can_fail_fast_when_worker_is_unavailable(self) -> None:
        created = self.service.create_task(
            "南京市面积是多少",
            "test-user",
            "worker-unavailable-key",
        )
        changed = self.service.fail_pending_without_worker(
            created["task_id"],
            "No active Worker heartbeat.",
        )
        task = self.service.get_task(created["task_id"])
        self.assertTrue(changed)
        self.assertEqual(task["status"], "FAILED")
        self.assertEqual(task["error_code"], "WORKER_UNAVAILABLE")
        self.assertIn("heartbeat", task["error_message"])


if __name__ == "__main__":
    unittest.main()
