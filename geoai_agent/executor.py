from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import env_int
from .errors import BudgetExceededError, classify_error
from .python_gis_tools import run_python_tool
from .qgis_runner import run_qgis_algorithm
from .task_workspace import TaskWorkspace
from .tool_registry import get_tool_config
from .workflow_schema import validate_task_schema, validate_workflow_schema


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ExecutionBudget:
    max_tool_calls: int = field(default_factory=lambda: env_int("AGENT_MAX_TOOL_CALLS", 20))
    deadline_seconds: int = field(default_factory=lambda: env_int("AGENT_DEADLINE_SECONDS", 900))
    started_at: float = field(default_factory=time.monotonic)
    calls: int = 0

    def consume(self) -> None:
        if self.calls >= self.max_tool_calls:
            raise BudgetExceededError("Maximum tool-call budget exceeded.")
        if time.monotonic() - self.started_at > self.deadline_seconds:
            raise BudgetExceededError("Task deadline exceeded.")
        self.calls += 1


def execute_task(
    task: dict,
    workspace: TaskWorkspace,
    budget: ExecutionBudget,
) -> dict[str, Any]:
    validate_task_schema(task)
    budget.consume()
    tool_name = task["tool"]
    config = get_tool_config(tool_name)
    params = workspace.resolve_params(task["params"])
    started_at = utc_now()
    started = time.monotonic()
    try:
        if config["backend"] == "python":
            result = run_python_tool(tool_name, params)
        else:
            result = run_qgis_algorithm(config["algorithm"], params)
        success = bool(result.get("success"))
        error_message = "" if success else (result.get("stderr") or "Tool execution failed.")
        error_type = None if success else (result.get("error_type") or classify_error(error_message))
    except Exception as exc:
        result = {
            "algorithm": config["algorithm"],
            "params": params,
            "returncode": 1,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "success": False,
            "metrics": {},
        }
        success = False
        error_message = str(exc)
        error_type = classify_error(exc)
    result.update({
        "tool": tool_name,
        "description": config["description"],
        "started_at": started_at,
        "finished_at": utc_now(),
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
        "error_type": error_type,
        "error_message": error_message,
    })
    return result


def execute_workflow(
    workflow: dict,
    workspace: TaskWorkspace,
    budget: ExecutionBudget | None = None,
) -> dict[str, Any]:
    validate_workflow_schema(workflow)
    active_budget = budget or ExecutionBudget()
    results = []
    for index, task in enumerate(workflow["steps"], start=1):
        result = execute_task(task, workspace, active_budget)
        result["step"] = index
        results.append(result)
        if not result["success"]:
            break
    success = len(results) == len(workflow["steps"]) and all(
        result["success"] for result in results
    )
    return {
        "workflow": workflow["workflow"],
        "task_id": workspace.task_id,
        "workspace": str(workspace.root),
        "success": success,
        "tool_calls_used": active_budget.calls,
        "steps": results,
        "error_type": next((r["error_type"] for r in results if not r["success"]), None),
        "error_message": next((r["error_message"] for r in results if not r["success"]), ""),
    }


def save_trace(trace: dict, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
