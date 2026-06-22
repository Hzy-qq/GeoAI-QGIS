from pathlib import Path
import json
import os
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
EVAL_CASES_PATH = PROJECT_ROOT / "evals" / "eval_cases.json"

sys.path.insert(0, str(PROJECT_ROOT))

from geoai_agent.chroma_rag_planner import plan_workflow_with_chroma_rag
from geoai_agent.chroma_store import ChromaStoreError
from geoai_agent.llm_client import LLMClientError
from geoai_agent.workflow_schema import WorkflowSchemaError, extract_workflow_tools, validate_planner_output


def load_eval_cases(path: Path = EVAL_CASES_PATH) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def evaluate_case(case: dict) -> dict:
    errors = []
    try:
        plan = plan_workflow_with_chroma_rag(case["query"])
        validate_planner_output(plan)
    except ChromaStoreError as exc:
        return {
            "id": case.get("id", "unnamed_case"),
            "query": case["query"],
            "passed": False,
            "errors": [f"chroma error: {exc}"],
            "plan": None,
        }
    except LLMClientError as exc:
        return {
            "id": case.get("id", "unnamed_case"),
            "query": case["query"],
            "passed": False,
            "errors": [f"llm api error: {exc}"],
            "plan": None,
        }
    except Exception as exc:
        return {
            "id": case.get("id", "unnamed_case"),
            "query": case["query"],
            "passed": False,
            "errors": [f"planner error: {exc}"],
            "plan": None,
        }

    if plan.get("supported") != case["expected_supported"]:
        errors.append(
            f"supported mismatch: expected {case['expected_supported']}, got {plan.get('supported')}"
        )

    if case["expected_supported"] and plan.get("supported"):
        expected_distance = case.get("expected_distance_meters")
        if expected_distance is not None and plan.get("distance_meters") != expected_distance:
            errors.append(
                f"distance mismatch: expected {expected_distance}, got {plan.get('distance_meters')}"
            )

        expected_tools = case.get("expected_tools")
        if expected_tools is not None:
            try:
                actual_tools = extract_workflow_tools(plan["workflow"])
            except WorkflowSchemaError as exc:
                errors.append(f"workflow schema error: {exc}")
            else:
                if actual_tools != expected_tools:
                    errors.append(f"tools mismatch: expected {expected_tools}, got {actual_tools}")

    return {
        "id": case.get("id", "unnamed_case"),
        "query": case["query"],
        "passed": not errors,
        "errors": errors,
        "plan": plan,
    }


def main() -> None:
    os.chdir(PROJECT_ROOT)
    results = []
    for case in load_eval_cases():
        result = evaluate_case(case)
        results.append(result)
        status = "PASS" if result["passed"] else "FAIL"
        print(f"[{status}] {result['id']}: {result['query']}")
        for error in result["errors"]:
            print("  -", error)

    passed_count = sum(result["passed"] for result in results)
    total_count = len(results)
    print()
    print(f"Chroma RAG planner eval: {passed_count}/{total_count} passed")
    if passed_count != total_count:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
