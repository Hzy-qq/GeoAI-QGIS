from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def run_unit_tests() -> int:
    return subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=PROJECT_ROOT,
        check=False,
    ).returncode


def check_runtime() -> int:
    from backend.database import Database
    from geoai_agent.config import load_dotenv
    from geoai_agent.qgis_runner import get_qgis_process_cmd

    load_dotenv()
    checks = {
        name: importlib.util.find_spec(name) is not None
        for name in (
            "geopandas", "chromadb", "sentence_transformers", "langgraph",
            "fastapi", "sqlalchemy", "uvicorn", "langgraph.checkpoint.sqlite",
        )
    }
    checks["LLM_API_KEY"] = bool(os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY"))
    checks["QGIS_PROCESS_CMD"] = Path(get_qgis_process_cmd()).exists()
    try:
        Database().check()
        checks["DATABASE"] = True
    except Exception:
        checks["DATABASE"] = False
    for name, passed in checks.items():
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
    return 0 if all(checks.values()) else 1


def evaluate_retrieval() -> int:
    from geoai_agent.chroma_store import retrieve_chroma_context_with_rerank

    cases = json.loads((PROJECT_ROOT / "evals" / "retrieval_eval_cases.json").read_text(encoding="utf-8"))
    recalls, reciprocal_ranks = [], []
    for case in cases:
        _, docs, _ = retrieve_chroma_context_with_rerank(case["query"], top_k=4)
        ids = [item["id"] for item in docs]
        relevant = set(case["relevant_ids"])
        recall = len(relevant.intersection(ids)) / len(relevant)
        rank = next((index for index, value in enumerate(ids, 1) if value in relevant), None)
        recalls.append(recall)
        reciprocal_ranks.append(1 / rank if rank else 0)
        print(f"[{case['id']}] recall@4={recall:.2f} ids={ids}")
    print(f"Mean recall@4={sum(recalls) / len(recalls):.3f}")
    print(f"MRR={sum(reciprocal_ranks) / len(reciprocal_ranks):.3f}")
    return 0


def evaluate_planner() -> int:
    from geoai_agent.llm_planner import plan_workflow_with_llm
    from geoai_agent.workflow_schema import validate_planner_output

    cases = json.loads((PROJECT_ROOT / "evals" / "planner_eval_cases.json").read_text(encoding="utf-8"))
    passed = 0
    for case in cases:
        try:
            plan = plan_workflow_with_llm(case["query"])
            validate_planner_output(plan)
            ok = all(plan.get(key) == value for key, value in case["expected"].items())
        except Exception as exc:
            print(f"[{case['id']}] {exc}")
            ok = False
        passed += int(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {case['id']}")
    return 0 if passed == len(cases) else 1


def evaluate_e2e() -> int:
    from geoai_agent.langgraph_agent import run_langgraph_agent

    cases = json.loads((PROJECT_ROOT / "evals" / "e2e_eval_cases.json").read_text(encoding="utf-8"))
    passed = 0
    for case in cases:
        trace = run_langgraph_agent(case["query"])
        ok = bool(
            trace.get("success")
            and (trace.get("plan") or {}).get("task_type") == case["expected_task_type"]
        )
        passed += int(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {case['id']} task_id={trace['task_id']}")
    return 0 if passed == len(cases) else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-runtime", action="store_true")
    parser.add_argument("--retrieval", action="store_true")
    parser.add_argument("--planner-live", action="store_true")
    parser.add_argument("--e2e-live", action="store_true")
    args = parser.parse_args()
    result = run_unit_tests()
    if args.check_runtime:
        result |= check_runtime()
    if args.retrieval:
        result |= evaluate_retrieval()
    if args.planner_live:
        result |= evaluate_planner()
    if args.e2e_live:
        result |= evaluate_e2e()
    raise SystemExit(1 if result else 0)


if __name__ == "__main__":
    main()
