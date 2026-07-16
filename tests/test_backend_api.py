from __future__ import annotations

import tempfile
import unittest
import warnings
from pathlib import Path


warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated",
)

try:
    from fastapi.testclient import TestClient

    from backend.api import create_app
except ImportError:  # pragma: no cover - dependency check handles this case
    TestClient = None
    create_app = None


@unittest.skipIf(TestClient is None, "FastAPI backend dependencies are not installed")
class BackendApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "api.db"
        self.client_context = TestClient(create_app(f"sqlite:///{database_path.as_posix()}"))
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def test_task_creation_is_idempotent(self) -> None:
        headers = {"Idempotency-Key": "same-request-001"}
        body = {"query": "南京市有多少个高校要素", "user_id": "test-user"}
        first = self.client.post("/api/v1/tasks", json=body, headers=headers)
        second = self.client.post("/api/v1/tasks", json=body, headers=headers)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json()["task_id"], second.json()["task_id"])
        self.assertEqual(first.json()["conversation_id"], second.json()["conversation_id"])
        self.assertFalse(first.json()["idempotency_reused"])
        self.assertTrue(second.json()["idempotency_reused"])

    def test_pending_task_has_no_result_yet(self) -> None:
        response = self.client.post(
            "/api/v1/tasks",
            json={"query": "南京市面积是多少", "user_id": "test-user"},
        )
        task_id = response.json()["task_id"]
        status_response = self.client.get(f"/api/v1/tasks/{task_id}")
        result_response = self.client.get(f"/api/v1/tasks/{task_id}/result")
        self.assertEqual(status_response.json()["status"], "PENDING")
        self.assertEqual(result_response.status_code, 409)
        self.assertEqual(result_response.json()["code"], "TASK_NOT_READY")

    def test_list_and_liveness(self) -> None:
        self.client.post(
            "/api/v1/tasks",
            json={"query": "南京市面积是多少", "user_id": "test-user"},
        )
        tasks = self.client.get("/api/v1/tasks?limit=10&offset=0")
        live = self.client.get("/health/live")
        self.assertEqual(tasks.status_code, 200)
        self.assertEqual(len(tasks.json()["items"]), 1)
        self.assertEqual(live.json()["status"], "ok")

    def test_explicit_conversation_collects_multiple_turns(self) -> None:
        conversation = self.client.post(
            "/api/v1/conversations",
            json={"user_id": "test-user", "title": "南京分析"},
        )
        self.assertEqual(conversation.status_code, 201)
        conversation_id = conversation.json()["conversation_id"]
        for query in ("计算南京市面积", "它周围有哪些城市"):
            response = self.client.post(
                "/api/v1/tasks",
                json={
                    "query": query,
                    "user_id": "test-user",
                    "conversation_id": conversation_id,
                },
            )
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["conversation_id"], conversation_id)
        messages = self.client.get(
            f"/api/v1/conversations/{conversation_id}/messages"
        )
        self.assertEqual(messages.status_code, 200)
        self.assertEqual(len(messages.json()["items"]), 2)
        self.assertTrue(all(item["role"] == "user" for item in messages.json()["items"]))
        conversations = self.client.get("/api/v1/conversations?user_id=test-user")
        self.assertEqual(conversations.status_code, 200)
        self.assertEqual(conversations.json()["items"][0]["conversation_id"], conversation_id)


if __name__ == "__main__":
    unittest.main()
