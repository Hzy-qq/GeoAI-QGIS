from __future__ import annotations

import unittest
import shutil
import uuid
from pathlib import Path
from unittest.mock import patch

from geoai_agent.conversation_agent import execute_node
from geoai_agent.context_resolver import resolve_conversation_context


class ConversationContextTests(unittest.TestCase):
    def test_three_turn_nanjing_acceptance_chain(self) -> None:
        first = resolve_conversation_context("帮我计算南京市的面积", {})
        self.assertEqual(first["region_name"], "南京市")
        self.assertEqual(first["task_type"], "administrative_area")

        memory = {"current_region": first["region_name"]}
        second = resolve_conversation_context("它周围有哪些城市？", memory)
        self.assertEqual(second["region_name"], "南京市")
        self.assertEqual(second["task_type"], "adjacent_regions")
        self.assertEqual(second["resolution_source"], "memory")

        third = resolve_conversation_context("再统计这里面的高校数量", memory)
        self.assertEqual(third["region_name"], "南京市")
        self.assertEqual(third["task_type"], "university_count")
        self.assertIn("南京市", third["resolved_query"])

    def test_reference_without_thread_memory_requires_clarification(self) -> None:
        result = resolve_conversation_context("它周围有哪些城市？", {})
        self.assertEqual(result["action"], "clarify")
        self.assertIn("具体指哪个", result["clarification"])

    def test_explicit_region_overrides_old_memory(self) -> None:
        result = resolve_conversation_context(
            "计算上海市面积", {"current_region": "南京市"},
        )
        self.assertEqual(result["region_name"], "上海市")
        self.assertEqual(result["resolution_source"], "explicit")

    @patch("geoai_agent.langgraph_agent.run_langgraph_agent")
    def test_execute_node_saves_inner_trace_in_workspace(self, mock_run) -> None:
        task_id = f"test-conversation-{uuid.uuid4().hex}"
        mock_run.return_value = {
            "plan": {
                "task_type": "administrative_area",
                "region_name": "南京市",
                "data_requirements": ["administrative_boundary"],
            },
            "summary": {"answer": "完成"},
            "evaluation_result": {"result_file": "result.gpkg"},
            "success": True,
        }
        result = execute_node({
            "task_id": task_id,
            "resolved_query": "计算南京市的面积",
            "region_name": "南京市",
            "task_type": "administrative_area",
        })
        trace_path = Path(result["inner_trace_path"])
        try:
            self.assertTrue(trace_path.exists())
            self.assertEqual(trace_path.parent.name, "trace")
        finally:
            shutil.rmtree(trace_path.parents[1], ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
