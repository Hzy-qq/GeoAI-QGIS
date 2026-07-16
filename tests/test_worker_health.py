from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.worker_health import read_worker_health, write_worker_heartbeat


class WorkerHealthTests(unittest.TestCase):
    def test_missing_heartbeat_is_not_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "missing.json"
            with patch.dict(
                os.environ,
                {"WORKER_HEARTBEAT_PATH": str(path), "REDIS_URL": ""},
            ):
                health = read_worker_health(timeout_seconds=10)
            self.assertFalse(health["active"])
            self.assertEqual(health["state"], "missing")

    def test_fresh_worker_heartbeat_is_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "worker.json"
            with patch.dict(
                os.environ,
                {"WORKER_HEARTBEAT_PATH": str(path), "REDIS_URL": ""},
            ):
                write_worker_heartbeat("worker-test", "running", current_task_id="task-1")
                health = read_worker_health(timeout_seconds=10)
            self.assertTrue(health["active"])
            self.assertTrue(health["ready"])
            self.assertEqual(health["current_task_id"], "task-1")


if __name__ == "__main__":
    unittest.main()
