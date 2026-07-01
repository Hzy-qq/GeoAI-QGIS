from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geoai_agent.langgraph_agent import run_langgraph_agent


def main() -> None:
    parser = argparse.ArgumentParser(description="Live LLM + network + GIS end-to-end evaluation.")
    parser.add_argument("--confirm-live", action="store_true")
    args = parser.parse_args()
    if not args.confirm_live:
        raise SystemExit("This eval uses the LLM API and live OSM services. Re-run with --confirm-live.")
    cases = json.loads((PROJECT_ROOT / "evals" / "e2e_eval_cases.json").read_text(encoding="utf-8"))
    passed = 0
    for case in cases:
        trace = run_langgraph_agent(case["query"])
        ok = trace.get("success") and (trace.get("plan") or {}).get("task_type") == case["expected_task_type"]
        passed += int(bool(ok))
        print(f"[{'PASS' if ok else 'FAIL'}] {case['id']} task_id={trace['task_id']}")
    print(f"Live E2E: {passed}/{len(cases)} passed")
    raise SystemExit(0 if passed == len(cases) else 1)


if __name__ == "__main__":
    main()
