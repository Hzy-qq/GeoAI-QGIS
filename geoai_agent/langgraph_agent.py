from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, TypedDict

from .chroma_store import retrieve_chroma_context_with_rerank
from .config import env_bool, env_int
from .dataset_resolver import resolve_data_requirements
from .executor import ExecutionBudget, execute_workflow
from .llm_planner import plan_workflow_with_llm
from .result_summarizer import summarize_workflow_result
from .progress import append_progress
from .task_workspace import TaskWorkspace
from .workflow_evaluator import evaluate_workflow_result
from .workflow_schema import WorkflowSchemaError, validate_planner_output


LOGGER = logging.getLogger(__name__)


class LangGraphAgentState(TypedDict, total=False):
    user_query: str
    task_id: str
    top_k: int
    retrieved_context: str
    retrieved_docs: list[dict[str, Any]]
    retrieval_metadata: dict[str, Any]
    resolved_datasets: list[dict[str, Any]]
    plan: dict[str, Any] | None
    workflow: dict[str, Any] | None
    validation_error: str | None
    execution_trace: dict[str, Any] | None
    evaluation_result: dict[str, Any] | None
    summary: dict[str, Any] | None
    success: bool
    attempt_count: int
    execution_attempt_count: int
    max_attempts: int
    node_trace: list[dict[str, Any]]


def _event(state: LangGraphAgentState, node: str, started: float, status: str, **extra) -> list[dict]:
    item = {
        "node": node,
        "status": status,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
        **extra,
    }
    task_id = state.get("task_id")
    if task_id:
        append_progress(task_id, item)
    return [*state.get("node_trace", []), item]


def retrieve_node(state: LangGraphAgentState) -> dict[str, Any]:
    started = time.monotonic()
    max_attempts = max(1, env_int("RAG_RETRIEVAL_MAX_ATTEMPTS", 2))
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            context, docs, metadata = retrieve_chroma_context_with_rerank(
                state["user_query"], top_k=state.get("top_k", 4),
            )
            break
        except Exception as exc:
            last_error = exc
            LOGGER.warning(
                "RAG retrieval attempt %s/%s failed: %s",
                attempt,
                max_attempts,
                exc,
            )
    else:
        # RAG improves the plan but is not a hard dependency of the deterministic
        # GIS tool chain. A stale/closed Chroma or model-download HTTP client must
        # not prevent a supported spatial analysis from running.
        error = str(last_error or "unknown retrieval error")
        context, docs = "", []
        metadata = {
            "enabled": False,
            "degraded": True,
            "fallback": "empty_context",
            "attempts": max_attempts,
            "error": error,
        }
    return {
        "retrieved_context": context,
        "retrieved_docs": docs,
        "retrieval_metadata": metadata,
        "node_trace": _event(
            state,
            "retrieve",
            started,
            "success",
            documents=len(docs),
            degraded=bool(metadata.get("degraded")),
            attempts=int(metadata.get("attempts", 1)),
            error=metadata.get("error"),
        ),
    }


def planner_node(state: LangGraphAgentState) -> dict[str, Any]:
    started = time.monotonic()
    feedback = state.get("validation_error")
    if not feedback and state.get("execution_trace"):
        feedback = state["execution_trace"].get("error_message")
    attempt = state.get("attempt_count", 0) + 1
    try:
        plan = plan_workflow_with_llm(
            state["user_query"],
            extra_context=state.get("retrieved_context", ""),
            feedback=feedback,
        )
        plan["retrieved_context"] = state.get("retrieved_docs", [])
        retrieval_metadata = state.get("retrieval_metadata", {})
        plan["retriever"] = (
            "chroma_cross_encoder"
            if retrieval_metadata.get("enabled", True)
            else "empty_context_fallback"
        )
        plan["retrieval_degraded"] = bool(retrieval_metadata.get("degraded"))
        return {
            "plan": plan,
            "attempt_count": attempt,
            "validation_error": None,
            "node_trace": _event(state, "plan", started, "success", attempt=attempt),
        }
    except Exception as exc:
        return {
            "plan": None,
            "attempt_count": attempt,
            "validation_error": str(exc),
            "node_trace": _event(state, "plan", started, "failed", attempt=attempt, error=str(exc)),
        }


def validator_node(state: LangGraphAgentState) -> dict[str, Any]:
    started = time.monotonic()
    plan = state.get("plan")
    if plan is None and state.get("validation_error"):
        # Preserve the actionable planner failure instead of replacing it with
        # the secondary and misleading "planner output must be an object".
        error = str(state["validation_error"])
        return {
            "validation_error": error,
            "workflow": None,
            "node_trace": _event(state, "validate", started, "failed", error=error),
        }
    try:
        validate_planner_output(plan)
    except (WorkflowSchemaError, ValueError) as exc:
        return {
            "validation_error": str(exc),
            "workflow": None,
            "node_trace": _event(state, "validate", started, "failed", error=str(exc)),
        }
    plan = state.get("plan") or {}
    workflow = plan.get("workflow") if plan.get("supported") else None
    return {
        "validation_error": None,
        "workflow": workflow,
        "resolved_datasets": resolve_data_requirements(plan) if plan.get("supported") else [],
        "node_trace": _event(state, "validate", started, "success"),
    }


def route_after_validation(state: LangGraphAgentState) -> str:
    if state.get("validation_error"):
        return "replan" if state.get("attempt_count", 0) < state.get("max_attempts", 2) else "error"
    if not (state.get("plan") or {}).get("supported"):
        return "unsupported"
    return "execute"


def executor_node(state: LangGraphAgentState) -> dict[str, Any]:
    started = time.monotonic()
    workspace = TaskWorkspace.create(state["task_id"])
    trace = execute_workflow(
        state["workflow"],
        workspace,
        ExecutionBudget(),
    )
    attempt = state.get("execution_attempt_count", 0) + 1
    return {
        "execution_trace": trace,
        "execution_attempt_count": attempt,
        "success": bool(trace.get("success")),
        "node_trace": _event(
            state, "execute", started,
            "success" if trace.get("success") else "failed",
            attempt=attempt,
            error_type=trace.get("error_type"),
        ),
    }


def route_after_execution(state: LangGraphAgentState) -> str:
    trace = state.get("execution_trace") or {}
    if trace.get("success"):
        return "evaluate"
    error_type = trace.get("error_type")
    failed_tool = next(
        (
            step.get("tool")
            for step in trace.get("steps", [])
            if not step.get("success")
        ),
        None,
    )
    # Road downloads already try multiple allowlisted endpoints within a strict
    # interactive deadline. Replaying the entire GIS workflow only doubles the wait.
    road_download_failed = failed_tool in {
        "download_osm_roads",
        "download_osm_roads_in_area",
    }
    if (
        error_type == "transient"
        and not road_download_failed
        and state.get("execution_attempt_count", 0) < 2
    ):
        return "retry_execute"
    if error_type == "plan_recoverable" and state.get("attempt_count", 0) < state.get("max_attempts", 2):
        return "replan"
    return "error"


def evaluator_node(state: LangGraphAgentState) -> dict[str, Any]:
    started = time.monotonic()
    workspace = TaskWorkspace.create(state["task_id"])
    evaluation = evaluate_workflow_result(
        state["workflow"], state["execution_trace"], workspace,
    )
    return {
        "evaluation_result": evaluation,
        "success": bool(evaluation.get("passed")),
        "node_trace": _event(
            state, "evaluate", started,
            "success" if evaluation.get("passed") else "failed",
            issues=evaluation.get("issues", []),
        ),
    }


def route_after_evaluation(state: LangGraphAgentState) -> str:
    return "summarize" if (state.get("evaluation_result") or {}).get("passed") else "error"


def summarizer_node(state: LangGraphAgentState) -> dict[str, Any]:
    started = time.monotonic()
    workspace = TaskWorkspace.create(state["task_id"])
    summary = summarize_workflow_result(
        state["user_query"],
        state["plan"],
        workspace,
        use_llm=env_bool("USE_LLM_SUMMARY", True),
    )
    success = summary is not None
    return {
        "summary": summary,
        "success": success,
        "node_trace": _event(state, "summarize", started, "success" if success else "failed"),
    }


def unsupported_node(state: LangGraphAgentState) -> dict[str, Any]:
    reason = (state.get("plan") or {}).get("reason") or "当前任务不在支持范围内。"
    return {
        "success": False,
        "summary": {"answer": f"当前无法执行该任务：{reason}", "answer_source": "unsupported"},
    }


def error_node(state: LangGraphAgentState) -> dict[str, Any]:
    execution = state.get("execution_trace") or {}
    message = (
        state.get("validation_error")
        or execution.get("error_message")
        or "; ".join((state.get("evaluation_result") or {}).get("issues", []))
        or "Agent execution failed."
    )
    return {
        "success": False,
        "summary": {"answer": f"任务执行失败：{message}", "answer_source": "error"},
    }


def build_langgraph_app():
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise RuntimeError("Install dependencies with: pip install -r requirements-advanced.txt") from exc
    graph = StateGraph(LangGraphAgentState)
    for name, node in (
        ("retrieve", retrieve_node), ("plan", planner_node), ("validate", validator_node),
        ("execute", executor_node), ("evaluate", evaluator_node), ("summarize", summarizer_node),
        ("unsupported", unsupported_node), ("error", error_node),
    ):
        graph.add_node(name, node)
    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "plan")
    graph.add_edge("plan", "validate")
    graph.add_conditional_edges(
        "validate", route_after_validation,
        {"replan": "plan", "execute": "execute", "unsupported": "unsupported", "error": "error"},
    )
    graph.add_conditional_edges(
        "execute", route_after_execution,
        {"retry_execute": "execute", "replan": "plan", "evaluate": "evaluate", "error": "error"},
    )
    graph.add_conditional_edges(
        "evaluate", route_after_evaluation,
        {"summarize": "summarize", "error": "error"},
    )
    for node in ("summarize", "unsupported", "error"):
        graph.add_edge(node, END)
    return graph.compile()


def run_langgraph_agent(
    user_query: str,
    top_k: int = 4,
    task_id: str | None = None,
) -> dict[str, Any]:
    workspace = TaskWorkspace.create(task_id)
    app = build_langgraph_app()
    state = app.invoke(
        {
            "user_query": user_query,
            "task_id": workspace.task_id,
            "top_k": top_k,
            "success": False,
            "attempt_count": 0,
            "execution_attempt_count": 0,
            "max_attempts": env_int("AGENT_MAX_PLAN_ATTEMPTS", 2),
            "node_trace": [],
        },
        config={"recursion_limit": env_int("LANGGRAPH_RECURSION_LIMIT", 20)},
    )
    return {
        "agent": "GeoAI-QGIS Final GIS LangGraph Agent",
        "task_id": workspace.task_id,
        "workspace": str(workspace.root),
        "user_query": user_query,
        "retrieved_docs": state.get("retrieved_docs", []),
        "retrieval_metadata": state.get("retrieval_metadata", {}),
        "resolved_datasets": state.get("resolved_datasets", []),
        "plan": state.get("plan"),
        "validation_error": state.get("validation_error"),
        "workflow": state.get("workflow"),
        "execution_trace": state.get("execution_trace"),
        "evaluation_result": state.get("evaluation_result"),
        "summary": state.get("summary"),
        "attempt_count": state.get("attempt_count", 0),
        "node_trace": state.get("node_trace", []),
        "success": bool(state.get("success")),
    }
