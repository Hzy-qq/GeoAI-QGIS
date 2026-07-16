import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.api import create_app
from geoai_agent.progress import append_progress, read_progress


class State6ApiTests(unittest.TestCase):
    def test_visual_page_and_state6_routes_are_registered(self):
        with tempfile.TemporaryDirectory() as temp:
            app = create_app(f"sqlite:///{Path(temp) / 'state6.db'}")
            with TestClient(app) as client:
                page = client.get("/")
                self.assertEqual(page.status_code, 200)
                self.assertIn("最近会话", page.text)
                self.assertIn("messages", page.text)
                self.assertIn("loadConversations", page.text)
                paths = client.get("/openapi.json").json()["paths"]
                self.assertIn("/api/v1/conversations", paths)
                self.assertIn("/api/v1/tasks/{task_id}/events", paths)
                self.assertIn(
                    "/api/v1/tasks/{task_id}/artifacts/{artifact_id}/geojson", paths
                )

    def test_progress_log_is_incrementally_readable(self):
        task_id = "state6-progress-test"
        append_progress(task_id, {"node": "retrieve", "status": "success"})
        events, cursor = read_progress(task_id)
        self.assertEqual(events[-1]["node"], "retrieve")
        self.assertGreater(cursor, 0)
        later, later_cursor = read_progress(task_id, cursor)
        self.assertEqual(later, [])
        self.assertEqual(later_cursor, cursor)

    def test_sse_fails_pending_task_when_worker_heartbeat_is_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app = create_app(f"sqlite:///{root / 'state6.db'}")
            with TestClient(app) as client:
                created = client.post(
                    "/api/v1/tasks",
                    json={"query": "计算南京市面积", "user_id": "test-user"},
                ).json()
                environment = {
                    "WORKER_HEARTBEAT_PATH": str(root / "missing-heartbeat.json"),
                    "TASK_PENDING_WORKER_GRACE_SECONDS": "0.5",
                }
                with patch.dict(os.environ, environment):
                    events = client.get(
                        f"/api/v1/tasks/{created['task_id']}/events?poll_seconds=0.25"
                    )
                task = client.get(f"/api/v1/tasks/{created['task_id']}").json()
                self.assertEqual(events.status_code, 200)
                self.assertIn("WORKER_UNAVAILABLE", events.text)
                self.assertIn("event: complete", events.text)
                self.assertEqual(task["status"], "FAILED")


if __name__ == "__main__":
    unittest.main()
