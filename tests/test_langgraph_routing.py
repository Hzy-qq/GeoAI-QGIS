from __future__ import annotations

import unittest

from geoai_agent.langgraph_agent import (
    route_after_execution,
    route_after_validation,
    validator_node,
)


class LangGraphRoutingTests(unittest.TestCase):
    def test_validator_preserves_planner_error_when_plan_is_missing(self) -> None:
        result = validator_node({
            "plan": None,
            "validation_error": "workflow step is missing INPUT",
            "node_trace": [],
        })
        self.assertEqual(result["validation_error"], "workflow step is missing INPUT")

    def test_validation_routes(self) -> None:
        self.assertEqual(route_after_validation({"plan": {"supported": True}}), "execute")
        self.assertEqual(route_after_validation({"plan": {"supported": False}}), "unsupported")
        self.assertEqual(route_after_validation({"validation_error": "bad", "attempt_count": 1, "max_attempts": 2}), "replan")
        self.assertEqual(route_after_validation({"validation_error": "bad", "attempt_count": 2, "max_attempts": 2}), "error")

    def test_execution_retry_is_bounded(self) -> None:
        state = {"execution_trace": {"success": False, "error_type": "transient"}, "execution_attempt_count": 1}
        self.assertEqual(route_after_execution(state), "retry_execute")
        state["execution_attempt_count"] = 2
        self.assertEqual(route_after_execution(state), "error")

    def test_road_download_failure_does_not_repeat_entire_workflow(self) -> None:
        state = {
            "execution_trace": {
                "success": False,
                "error_type": "transient",
                "steps": [{"tool": "download_osm_roads", "success": False}],
            },
            "execution_attempt_count": 1,
        }
        self.assertEqual(route_after_execution(state), "error")


if __name__ == "__main__":
    unittest.main()
