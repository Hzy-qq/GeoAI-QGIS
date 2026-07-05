from __future__ import annotations

from typing import Any

from .chroma_store import get_embedding_model_name
from .llm_client import get_llm_model, get_llm_provider


CODE_VERSION = "0.7.0-state5"
PROMPT_VERSION = "planner-v3-dynamic-data-tool-call"
KNOWLEDGE_BASE_VERSION = "geoai-kb-v3-dynamic-data-no-eval"
EVAL_SCHEMA_VERSION = "stage4-v1"


def get_runtime_versions() -> dict[str, Any]:
    return {
        "code_version": CODE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "knowledge_base_version": KNOWLEDGE_BASE_VERSION,
        "eval_schema_version": EVAL_SCHEMA_VERSION,
        "llm_provider": get_llm_provider(),
        "llm_model": get_llm_model(),
        "embedding_model": get_embedding_model_name(),
    }
