from __future__ import annotations

from functools import lru_cache
from typing import Any

from .config import env_bool, env_int, env_str


DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-base"


@lru_cache(maxsize=2)
def _load_cross_encoder(model_name: str):
    from sentence_transformers import CrossEncoder

    return CrossEncoder(model_name)


def rerank_documents(
    query: str,
    documents: list[dict[str, Any]],
    top_k: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    limit = top_k or env_int("RERANK_TOP_K", 4)
    if not documents:
        return [], {"enabled": False, "reason": "no_documents"}
    if not env_bool("RERANK_ENABLED", True):
        return documents[:limit], {"enabled": False, "reason": "disabled"}
    model_name = env_str("RERANK_MODEL", DEFAULT_RERANK_MODEL)
    try:
        model = _load_cross_encoder(model_name)
        pairs = [(query, item["text"]) for item in documents]
        scores = model.predict(pairs, show_progress_bar=False)
    except Exception as exc:
        if env_bool("RERANK_STRICT", False):
            raise RuntimeError(f"Reranker failed: {exc}") from exc
        fallback = documents[:limit]
        return fallback, {
            "enabled": False,
            "reason": "fallback",
            "model": model_name,
            "error": str(exc),
        }
    ranked = []
    for item, score in zip(documents, scores):
        candidate = dict(item)
        candidate["dense_score"] = item.get("score")
        candidate["rerank_score"] = round(float(score), 6)
        candidate["retriever"] = "chroma+cross_encoder"
        ranked.append(candidate)
    ranked.sort(key=lambda item: item["rerank_score"], reverse=True)
    return ranked[:limit], {
        "enabled": True,
        "model": model_name,
        "candidate_count": len(documents),
        "returned_count": min(limit, len(documents)),
    }


def build_context(results: list[dict[str, Any]]) -> str:
    parts = []
    for index, result in enumerate(results, start=1):
        source = result.get("metadata", {}).get("source", "unknown")
        score = result.get("rerank_score", result.get("score", 0))
        parts.append(f"[Retrieved {index} | score={score} | source={source}]\n{result['text']}")
    return "\n\n".join(parts)
