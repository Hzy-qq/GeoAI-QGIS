from __future__ import annotations

from .chroma_store import retrieve_chroma_context
from .llm_planner import plan_workflow_with_llm


def plan_workflow_with_chroma_rag(
    user_query: str,
    model: str | None = None,
    top_k: int = 4,
) -> dict:
    context, retrieved = retrieve_chroma_context(user_query, top_k=top_k)
    plan = plan_workflow_with_llm(
        user_query,
        model=model,
        extra_context=context,
    )
    plan["retrieved_context"] = retrieved
    plan["retriever"] = "chroma"
    return plan
