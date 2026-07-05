from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, TypedDict

from .config import PROJECT_ROOT, env_int, env_str
from .context_resolver import resolve_conversation_context
from .executor import save_trace
from .task_workspace import TaskWorkspace


class ConversationGraphState(TypedDict, total=False):
    conversation_id: str
    task_id: str
    user_query: str
    memory: dict[str, Any]
    conversation_summary: str
    recent_messages: list[dict[str, str]]
    action: str
    task_type: str
    region_name: str
    resolved_query: str
    clarification: str
    resolution_source: str
    inner_trace_path: str
    memory_update: dict[str, Any]
    answer: str
    success: bool


_APP = None
_APP_LOCK = threading.Lock()
_CHECKPOINT_CONNECTION: sqlite3.Connection | None = None


def resolve_context_node(state: ConversationGraphState) -> dict[str, Any]:
    return resolve_conversation_context(state["user_query"], state.get("memory"))


def route_after_context(state: ConversationGraphState) -> str:
    return "clarify" if state.get("action") == "clarify" else "execute"


def clarify_node(state: ConversationGraphState) -> dict[str, Any]:
    return {
        "answer": state["clarification"],
        "success": True,
        "inner_trace_path": "",
        "memory_update": {},
    }


def execute_node(state: ConversationGraphState) -> dict[str, Any]:
    from .langgraph_agent import run_langgraph_agent

    trace = run_langgraph_agent(state["resolved_query"], task_id=state["task_id"])
    workspace = TaskWorkspace.create(state["task_id"])
    inner_trace_path = workspace.root / "trace" / "inner_agent_trace.json"
    save_trace(trace, inner_trace_path)

    plan = trace.get("plan") or {}
    summary = trace.get("summary") or {}
    evaluation = trace.get("evaluation_result") or {}
    memory_update = {
        "current_region": plan.get("region_name") or state.get("region_name") or "",
        "current_dataset": plan.get("data_requirements") or [],
        "previous_task_type": plan.get("task_type") or state.get("task_type") or "",
        "previous_result": summary.get("answer") or "",
        "previous_artifact": evaluation.get("result_file") or "",
    }
    return {
        "answer": summary.get("answer") or "任务未生成回答。",
        "success": bool(trace.get("success")),
        "inner_trace_path": str(inner_trace_path),
        "memory_update": memory_update,
    }


def _checkpoint_saver():
    global _CHECKPOINT_CONNECTION
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        raise RuntimeError(
            "Missing langgraph-checkpoint-sqlite. Install requirements.txt first."
        ) from exc
    path = Path(env_str("LANGGRAPH_CHECKPOINT_PATH", "outputs/checkpoints/state5.sqlite"))
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    _CHECKPOINT_CONNECTION = sqlite3.connect(path, check_same_thread=False)
    saver = SqliteSaver(_CHECKPOINT_CONNECTION)
    saver.setup()
    return saver


def build_conversation_app(checkpointer=None):
    from langgraph.graph import END, StateGraph

    graph = StateGraph(ConversationGraphState)
    graph.add_node("resolve_context", resolve_context_node)
    graph.add_node("clarify", clarify_node)
    graph.add_node("execute", execute_node)
    graph.set_entry_point("resolve_context")
    graph.add_conditional_edges(
        "resolve_context", route_after_context, {"clarify": "clarify", "execute": "execute"},
    )
    graph.add_edge("clarify", END)
    graph.add_edge("execute", END)
    return graph.compile(checkpointer=checkpointer)


def get_conversation_app():
    global _APP
    if _APP is None:
        with _APP_LOCK:
            if _APP is None:
                _APP = build_conversation_app(_checkpoint_saver())
    return _APP


def run_conversation_turn(
    *,
    conversation_id: str,
    task_id: str,
    user_query: str,
    memory: dict[str, Any] | None = None,
    conversation_summary: str = "",
    recent_messages: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    state = get_conversation_app().invoke(
        {
            "conversation_id": conversation_id,
            "task_id": task_id,
            "user_query": user_query,
            "memory": memory or {},
            "conversation_summary": conversation_summary,
            "recent_messages": recent_messages or [],
            "action": "",
            "task_type": "",
            "region_name": "",
            "resolved_query": "",
            "clarification": "",
            "resolution_source": "",
            "inner_trace_path": "",
            "memory_update": {},
            "answer": "",
            "success": False,
        },
        config={
            "configurable": {"thread_id": conversation_id},
            "recursion_limit": env_int("LANGGRAPH_RECURSION_LIMIT", 20),
        },
    )
    return dict(state)


def load_inner_trace(result: dict[str, Any]) -> dict[str, Any] | None:
    path_value = result.get("inner_trace_path")
    if not path_value:
        return None
    path = Path(path_value)
    if not path.exists():
        raise FileNotFoundError(f"Conversation inner trace does not exist: {path}")
    return json.loads(path.read_text(encoding="utf-8"))
