from pathlib import Path
import json
import os
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
TRACE_PATH = PROJECT_ROOT / "outputs" / "langgraph_chroma_agent_trace.json"

sys.path.insert(0, str(PROJECT_ROOT))

from geoai_agent.chroma_store import ChromaStoreError
from geoai_agent.langgraph_agent import run_langgraph_agent


def main() -> None:
    os.chdir(PROJECT_ROOT)
    user_query = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else input("请输入空间分析任务：")
    try:
        trace = run_langgraph_agent(user_query, top_k=4)
    except (ChromaStoreError, RuntimeError) as exc:
        print("LangGraph + Chroma agent failed:", exc)
        raise SystemExit(1) from exc

    TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TRACE_PATH.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")

    plan = trace.get("plan")
    if plan:
        print("LangGraph planner generated workflow:", plan.get("workflow", {}).get("workflow"))
        print("Retriever:", plan.get("retriever"))
        print("Retrieved docs:", len(trace.get("retrieved_docs", [])))

    validation_error = trace.get("validation_error")
    if validation_error:
        print("Validation error:", validation_error)

    execution_trace = trace.get("execution_trace")
    if execution_trace:
        print("Workflow success:", execution_trace.get("success"))
        for step in execution_trace.get("steps", []):
            print("Step", step["step"], step["tool"], "success:", step["success"])

    summary = trace.get("summary")
    if summary:
        print()
        print("最终回答：")
        print(summary["answer"])

    print("LangGraph + Chroma agent trace saved to", TRACE_PATH)


if __name__ == "__main__":
    main()
