from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .versioning import get_runtime_versions


def load_json_cases(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Evaluation cases must be a JSON array: {path}")
    return data


def new_report(suite: str, case_path: Path) -> dict[str, Any]:
    return {
        "suite": suite,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_file": str(case_path),
        "versions": get_runtime_versions(),
        "metrics": {},
        "cases": [],
        "passed": False,
    }


def save_report(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def safe_ratio(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator), 4)
