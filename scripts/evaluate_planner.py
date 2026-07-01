from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geoai_agent.llm_planner import plan_workflow_with_llm
from geoai_agent.workflow_schema import validate_planner_output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=PROJECT_ROOT / "evals" / "planner_eval_cases.json")
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    passed = 0
    for case in cases:
        try:
            plan = plan_workflow_with_llm(case["query"])
            validate_planner_output(plan)
            errors = [
                f"{key}: expected {value!r}, got {plan.get(key)!r}"
                for key, value in case["expected"].items()
                if plan.get(key) != value
            ]
        except Exception as exc:
            errors = [str(exc)]
        ok = not errors
        passed += int(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {case['id']}")
        for message in errors:
            print("  -", message)
    print(f"Planner eval: {passed}/{len(cases)} passed")
    raise SystemExit(0 if passed == len(cases) else 1)


if __name__ == "__main__":
    main()
