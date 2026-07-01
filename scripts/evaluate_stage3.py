from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run(script: str, *args: str) -> int:
    return subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / script), *args],
        cwd=PROJECT_ROOT,
        check=False,
    ).returncode


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--retrieval", action="store_true", help="Run Chroma + reranker eval.")
    parser.add_argument("--planner-live", action="store_true", help="Use the LLM API.")
    parser.add_argument("--e2e-live", action="store_true", help="Use LLM, OSM and QGIS.")
    args = parser.parse_args()
    failures = run("run_tests.py")
    if args.retrieval:
        failures |= run("evaluate_retrieval.py")
    if args.planner_live:
        failures |= run("evaluate_planner.py")
    if args.e2e_live:
        failures |= run("evaluate_e2e.py", "--confirm-live")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
