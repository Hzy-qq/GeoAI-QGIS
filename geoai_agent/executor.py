from pathlib import Path
import json

from .qgis_runner import run_qgis_algorithm
from .tool_registry import get_tool_config


def validate_task(task: dict) -> None:
    if not isinstance(task, dict):
        raise ValueError("Task must be a dictionary.")
    if "tool" not in task:
        raise ValueError("Task must contain a 'tool' field.")
    if "params" not in task:
        raise ValueError("Task must contain a 'params' field.")
    if not isinstance(task["params"], dict):
        raise ValueError("Task 'params' must be a dictionary.")

    tool_config = get_tool_config(task["tool"])
    required_params = tool_config["required_params"]
    params = task["params"]
    missing_params = [
        name for name in required_params
        if name not in params or params[name] in ("", None)
    ]
    if missing_params:
        raise ValueError(
            f"Missing required params for tool '{task['tool']}': {missing_params}"
        )


def execute_task(task: dict) -> dict:
    validate_task(task)
    tool_name = task["tool"]
    params = task["params"]
    tool_config = get_tool_config(tool_name)
    result = run_qgis_algorithm(tool_config["algorithm"], params)
    result["tool"] = tool_name
    result["description"] = tool_config["description"]
    return result


def execute_workflow(workflow: dict) -> dict:
    if not isinstance(workflow, dict):
        raise ValueError("Workflow must be a dictionary.")
    if "steps" not in workflow:
        raise ValueError("Workflow must contain a 'steps' field.")
    if not isinstance(workflow["steps"], list):
        raise ValueError("Workflow 'steps' must be a list.")

    results = []
    for index, task in enumerate(workflow["steps"], start=1):
        result = execute_task(task)
        result["step"] = index
        results.append(result)
        if not result["success"]:
            break

    return {
        "workflow": workflow.get("workflow", "unnamed_workflow"),
        "success": all(result["success"] for result in results),
        "steps": results,
    }


def save_trace(trace: dict, output_path: str) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(trace, f, ensure_ascii=False, indent=2)
