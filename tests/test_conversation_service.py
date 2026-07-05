from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from backend.database import Database
from backend.service import TaskService
from geoai_agent import conversation_agent


class ConversationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.old_checkpoint = os.environ.get("LANGGRAPH_CHECKPOINT_PATH")
        os.environ["LANGGRAPH_CHECKPOINT_PATH"] = str(root / "checkpoints.sqlite")
        conversation_agent._APP = None
        conversation_agent._CHECKPOINT_CONNECTION = None
        self.database = Database(f"sqlite:///{(root / 'service.db').as_posix()}")
        self.database.create_schema()
        self.service = TaskService(self.database)

    def tearDown(self) -> None:
        self.database.dispose()
        if conversation_agent._CHECKPOINT_CONNECTION is not None:
            conversation_agent._CHECKPOINT_CONNECTION.close()
        conversation_agent._CHECKPOINT_CONNECTION = None
        conversation_agent._APP = None
        if self.old_checkpoint is None:
            os.environ.pop("LANGGRAPH_CHECKPOINT_PATH", None)
        else:
            os.environ["LANGGRAPH_CHECKPOINT_PATH"] = self.old_checkpoint
        self.temp_dir.cleanup()

    def test_clarification_is_persisted_without_calling_llm(self) -> None:
        task = self.service.create_task(
            "它周围有哪些城市？", "test-user", "clarify-key-001",
        )
        trace = self.service.execute_claimed(task["task_id"], task["query"])
        self.assertTrue(trace["success"])
        self.assertEqual(trace["conversation"]["action"], "clarify")
        self.assertEqual(self.service.get_task(task["task_id"])["status"], "SUCCEEDED")

        conversation = self.service.get_conversation(task["conversation_id"])
        messages = self.service.get_conversation_messages(task["conversation_id"])
        self.assertEqual(conversation["turn_count"], 1)
        self.assertEqual([item["role"] for item in messages], ["user", "assistant"])
        self.assertTrue(Path(os.environ["LANGGRAPH_CHECKPOINT_PATH"]).exists())


if __name__ == "__main__":
    unittest.main()
