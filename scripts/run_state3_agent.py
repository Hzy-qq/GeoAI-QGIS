from __future__ import annotations

import json
import os
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from geoai_agent.chroma_store import ChromaStoreError
from geoai_agent.executor import save_trace
from geoai_agent.langgraph_agent import run_langgraph_agent


def main() -> None:
    os.chdir(PROJECT_ROOT)
    query = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else input("请输入空间分析任务：").strip()
    if not query:
        raise SystemExit("任务不能为空。")
    try:
        trace = run_langgraph_agent(query)
    except ChromaStoreError as exc:
        raise SystemExit(
            f"知识库不可用：{exc}\n请先运行 scripts/build_chroma_store.py"
        ) from exc

    task_root = Path(trace["workspace"])
    trace_path = task_root / "trace" / "agent_trace.json"
    save_trace(trace, trace_path)

    print("Task ID:", trace["task_id"])
    plan = trace.get("plan") or {}
    print("Planner mode:", plan.get("planner_mode", "unknown"))
    print("Task type:", plan.get("task_type", "unknown"))
    print("Region:", plan.get("region_name", ""))
    print("Data requirements:", ", ".join(plan.get("data_requirements", [])))
    retrieval = trace.get("retrieval_metadata", {})
    print("Reranker:", retrieval.get("model") if retrieval.get("enabled") else retrieval.get("reason"))
    execution = trace.get("execution_trace") or {}
    for step in execution.get("steps", []):
        print(f"Step {step['step']:02d} {step['tool']}: {step['success']}")
    evaluation = trace.get("evaluation_result") or {}
    if evaluation:
        print("Evaluator passed:", evaluation.get("passed"))
    summary = trace.get("summary") or {}
    print("\n最终回答：")
    print(summary.get("answer", "没有生成结果。"))
    print("\nTrace:", trace_path)


if __name__ == "__main__":
    main()
