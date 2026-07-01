from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geoai_agent.executor import execute_workflow
from geoai_agent.result_summarizer import summarize_workflow_result
from geoai_agent.task_workspace import TaskWorkspace
from geoai_agent.workflow_evaluator import evaluate_workflow_result
from geoai_agent.workflow_factory import build_dynamic_plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Live OSM + QGIS smoke test without LLM calls.")
    parser.add_argument("--region", default="南京市")
    parser.add_argument("--distance", type=int, default=500)
    args = parser.parse_args()
    workspace = TaskWorkspace.create()
    plan = build_dynamic_plan(
        "road_length_around_poi",
        args.region,
        distance_meters=args.distance,
    )
    trace = execute_workflow(plan["workflow"], workspace)
    for step in trace["steps"]:
        print(f"Step {step['step']:02d} {step['tool']}: {step['success']}")
        if not step["success"]:
            print(step["error_message"])
    evaluation = evaluate_workflow_result(plan["workflow"], trace, workspace)
    print("Evaluator:", evaluation)
    if not evaluation["passed"]:
        raise SystemExit(1)
    summary = summarize_workflow_result(
        f"统计{args.region}所有大学周边{args.distance}米道路长度",
        plan,
        workspace,
        use_llm=False,
    )
    print("\n", summary["answer"])
    print("Task workspace:", workspace.root)


if __name__ == "__main__":
    main()
