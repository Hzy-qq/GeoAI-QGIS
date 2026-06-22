from .tool_registry import get_tool_config


class WorkflowSchemaError(ValueError):
    """Raised when planner output or workflow JSON is invalid."""


def _require_dict(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        raise WorkflowSchemaError(f"{name} must be a dictionary.")
    return value


def _require_non_empty_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowSchemaError(f"{name} must be a non-empty string.")
    return value


def validate_task_schema(task: dict, step_index: int | None = None, strict: bool = True) -> None:
    label = f"steps[{step_index}]" if step_index is not None else "task"
    task = _require_dict(task, label)

    tool_name = _require_non_empty_string(task.get("tool"), f"{label}.tool")
    params = _require_dict(task.get("params"), f"{label}.params")

    tool_config = get_tool_config(tool_name)
    required_params = set(tool_config["required_params"])
    optional_params = set(tool_config["optional_params"])
    allowed_params = required_params | optional_params

    missing_params = [
        name for name in tool_config["required_params"]
        if name not in params or params[name] in ("", None)
    ]
    if missing_params:
        raise WorkflowSchemaError(
            f"{label} missing required params for tool '{tool_name}': {missing_params}"
        )

    if strict:
        unknown_params = sorted(set(params) - allowed_params)
        if unknown_params:
            raise WorkflowSchemaError(
                f"{label} contains unknown params for tool '{tool_name}': {unknown_params}"
            )


def validate_workflow_schema(workflow: dict, strict: bool = True) -> None:
    workflow = _require_dict(workflow, "workflow")
    _require_non_empty_string(workflow.get("workflow"), "workflow.workflow")

    steps = workflow.get("steps")
    if not isinstance(steps, list) or not steps:
        raise WorkflowSchemaError("workflow.steps must be a non-empty list.")

    for index, task in enumerate(steps, start=1):
        validate_task_schema(task, step_index=index, strict=strict)


def validate_planner_output(plan: dict, strict: bool = True) -> None:
    plan = _require_dict(plan, "planner output")

    if "supported" not in plan or not isinstance(plan["supported"], bool):
        raise WorkflowSchemaError("planner output must contain boolean field 'supported'.")

    if plan["supported"]:
        distance_meters = plan.get("distance_meters")
        if not isinstance(distance_meters, int) or distance_meters <= 0:
            raise WorkflowSchemaError(
                "supported planner output must contain positive integer field 'distance_meters'."
            )
        validate_workflow_schema(plan.get("workflow"), strict=strict)
        return

    _require_non_empty_string(plan.get("reason"), "planner output.reason")


def extract_workflow_tools(workflow: dict) -> list[str]:
    validate_workflow_schema(workflow)
    return [step["tool"] for step in workflow["steps"]]
