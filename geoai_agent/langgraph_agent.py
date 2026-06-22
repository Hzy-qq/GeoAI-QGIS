from __future__ import annotations

from typing import Any, TypedDict

from .chroma_store import retrieve_chroma_context
from .executor import execute_workflow
from .llm_planner import plan_workflow_with_llm
from .result_summarizer import summarize_workflow_result
from .workflow_schema import WorkflowSchemaError, validate_planner_output


class LangGraphAgentState(TypedDict, total=False):
    user_query: str
    top_k: int
    retrieved_context: str
    retrieved_docs: list[dict[str, Any]]
    plan: dict[str, Any] | None
    workflow: dict[str, Any] | None
    validation_error: str | None
    execution_trace: dict[str, Any] | None
    summary: dict[str, Any] | None
    success: bool


def retrieve_node(state: LangGraphAgentState) -> dict[str, Any]:
    context, docs = retrieve_chroma_context(
        state["user_query"],
        top_k=state.get("top_k", 4),
    )
    return {
        "retrieved_context": context,
        "retrieved_docs": docs,
    }


def planner_node(state: LangGraphAgentState) -> dict[str, Any]:
    plan = plan_workflow_with_llm(
        state["user_query"],
        extra_context=state.get("retrieved_context", ""),
    )
    plan["retrieved_context"] = state.get("retrieved_docs", [])
    plan["retriever"] = "chroma"
    return {"plan": plan}


def validator_node(state: LangGraphAgentState) -> dict[str, Any]:
    try:
        validate_planner_output(state.get("plan"))
    except (WorkflowSchemaError, ValueError) as exc:
        return {
            "validation_error": str(exc),
            "workflow": None,
        }

    plan = state.get("plan") or {}
    workflow = plan["workflow"] if plan.get("supported") else None
    return {
        "validation_error": None,
        "workflow": workflow,
    }


def executor_node(state: LangGraphAgentState) -> dict[str, Any]:
    if state.get("validation_error") or not state.get("workflow"):
        return {
            "execution_trace": None,
            "success": False,
        }
    execution_trace = execute_workflow(state["workflow"])
    return {
        "execution_trace": execution_trace,
        "success": bool(execution_trace.get("success")),
    }


def summarizer_node(state: LangGraphAgentState) -> dict[str, Any]:
    if not state.get("success") or not state.get("workflow") or not state.get("plan"):
        return {"summary": None}
    summary = summarize_workflow_result(
        state["user_query"],
        state["workflow"],
        distance_meters=state["plan"].get("distance_meters"),
    )
    return {"summary": summary}


def build_langgraph_app():
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise RuntimeError(
            "langgraph is not installed. Install advanced dependencies with: "
            "pip install -r requirements-advanced.txt"
        ) from exc

    graph = StateGraph(LangGraphAgentState)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("plan", planner_node)
    graph.add_node("validate", validator_node)
    graph.add_node("execute", executor_node)
    graph.add_node("summarize", summarizer_node)

    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "plan")
    graph.add_edge("plan", "validate")
    graph.add_edge("validate", "execute")
    graph.add_edge("execute", "summarize")
    graph.add_edge("summarize", END)
    return graph.compile()


def run_langgraph_agent(user_query: str, top_k: int = 4) -> dict[str, Any]:
    app = build_langgraph_app()
    state = app.invoke({
        "user_query": user_query,
        "top_k": top_k,
        "success": False,
    })
    return {
        "agent": "GeoAI LangGraph + Chroma Agent",
        "user_query": state.get("user_query"),
        "retrieved_docs": state.get("retrieved_docs", []),
        "plan": state.get("plan"),
        "validation_error": state.get("validation_error"),
        "workflow": state.get("workflow"),
        "execution_trace": state.get("execution_trace"),
        "summary": state.get("summary"),
        "success": bool(state.get("success")),
    }
