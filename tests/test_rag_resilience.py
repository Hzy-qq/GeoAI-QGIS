from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from geoai_agent.langgraph_agent import retrieve_node


class RagResilienceTests(unittest.TestCase):
    @patch("geoai_agent.langgraph_agent.append_progress")
    @patch("geoai_agent.langgraph_agent.retrieve_chroma_context_with_rerank")
    def test_retrieve_node_degrades_instead_of_failing_gis_task(
        self,
        retrieval,
        _append_progress,
    ) -> None:
        retrieval.side_effect = RuntimeError(
            "Cannot send a request, as the client has been closed. in query."
        )
        state = {
            "user_query": "分析南京市医院1公里服务覆盖范围",
            "task_id": "rag-fallback-test",
            "node_trace": [],
        }

        with patch.dict(os.environ, {"RAG_RETRIEVAL_MAX_ATTEMPTS": "2"}):
            result = retrieve_node(state)

        self.assertEqual(retrieval.call_count, 2)
        self.assertEqual(result["retrieved_context"], "")
        self.assertEqual(result["retrieved_docs"], [])
        self.assertTrue(result["retrieval_metadata"]["degraded"])
        self.assertEqual(result["retrieval_metadata"]["fallback"], "empty_context")
        self.assertEqual(result["node_trace"][-1]["status"], "success")
        self.assertTrue(result["node_trace"][-1]["degraded"])

    @patch("geoai_agent.langgraph_agent.append_progress")
    @patch("geoai_agent.langgraph_agent.retrieve_chroma_context_with_rerank")
    def test_retrieve_node_recovers_on_second_attempt(
        self,
        retrieval,
        _append_progress,
    ) -> None:
        successful = (
            "retrieved context",
            [{"id": "doc-1", "text": "hospital buffer"}],
            {"enabled": True, "returned_count": 1},
        )
        retrieval.side_effect = [RuntimeError("temporary client failure"), successful]

        with patch.dict(os.environ, {"RAG_RETRIEVAL_MAX_ATTEMPTS": "2"}):
            result = retrieve_node(
                {
                    "user_query": "医院服务区",
                    "task_id": "rag-retry-test",
                    "node_trace": [],
                }
            )

        self.assertEqual(retrieval.call_count, 2)
        self.assertEqual(result["retrieved_context"], "retrieved context")
        self.assertTrue(result["retrieval_metadata"]["enabled"])
        self.assertFalse(result["node_trace"][-1]["degraded"])


if __name__ == "__main__":
    unittest.main()
