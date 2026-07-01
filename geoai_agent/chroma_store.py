from __future__ import annotations

import contextlib
import io
import logging
import os
from pathlib import Path
from typing import Any

from .config import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHROMA_PATH = PROJECT_ROOT / "outputs" / "chroma"
DEFAULT_HF_CACHE_PATH = PROJECT_ROOT / "outputs" / "hf_cache"
DEFAULT_COLLECTION_NAME = "geoai_knowledge"
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
MODEL_CACHE: dict[str, Any] = {}


class ChromaStoreError(RuntimeError):
    """Raised when Chroma or the embedding model is unavailable."""


def get_chroma_path() -> Path:
    load_dotenv()
    path = Path(os.getenv("CHROMA_PATH", str(DEFAULT_CHROMA_PATH)))
    return path if path.is_absolute() else PROJECT_ROOT / path


def get_chroma_collection_name() -> str:
    load_dotenv()
    return os.getenv("CHROMA_COLLECTION", DEFAULT_COLLECTION_NAME)


def get_embedding_model_name(model_name: str | None = None) -> str:
    load_dotenv()
    return model_name or os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)


def configure_huggingface_cache() -> None:
    load_dotenv()
    cache_path = Path(os.getenv("HF_HOME", str(DEFAULT_HF_CACHE_PATH)))
    cache_path.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(cache_path))
    os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(cache_path / "sentence_transformers"))
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")


def quiet_embedding_logs_enabled() -> bool:
    return os.getenv("QUIET_EMBEDDING_LOGS", "1").lower() not in {"0", "false", "no"}


def configure_embedding_loggers() -> None:
    for logger_name in (
        "sentence_transformers",
        "transformers",
        "huggingface_hub",
        "modelscope",
    ):
        logging.getLogger(logger_name).setLevel(logging.ERROR)

    try:
        from transformers.utils import logging as transformers_logging

        transformers_logging.set_verbosity_error()
    except Exception:
        pass

    if quiet_embedding_logs_enabled():
        for name in list(logging.root.manager.loggerDict):
            if name.startswith(("huggingface_hub", "sentence_transformers", "transformers")):
                logging.getLogger(name).setLevel(logging.CRITICAL)
                logging.getLogger(name).propagate = False


class SentenceTransformerEmbeddingFunction:
    """
    Chroma embedding function backed by sentence-transformers.

    The default model is a small Chinese/English retrieval model. You can
    override it with EMBEDDING_MODEL in .env or the shell.
    """

    def __init__(self, model_name: str | None = None):
        self.model_name = get_embedding_model_name(model_name)
        self._model = None

    def name(self) -> str:
        return f"sentence-transformers:{self.model_name}"

    def _load_model(self):
        if self._model is not None:
            return self._model
        if self.model_name in MODEL_CACHE:
            self._model = MODEL_CACHE[self.model_name]
            return self._model
        configure_huggingface_cache()
        configure_embedding_loggers()
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ChromaStoreError(
                "sentence-transformers is not installed. Install advanced "
                "dependencies with: pip install -r requirements-advanced.txt"
            ) from exc

        cache_folder = str(Path(os.environ["SENTENCE_TRANSFORMERS_HOME"]))
        base_kwargs = {"cache_folder": cache_folder}

        def load_sentence_transformer(**kwargs):
            if quiet_embedding_logs_enabled():
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    return SentenceTransformer(self.model_name, **kwargs)
            return SentenceTransformer(self.model_name, **kwargs)

        local_files_setting = os.getenv("EMBEDDING_LOCAL_FILES_ONLY", "auto").lower()
        if local_files_setting in {"1", "true", "yes"}:
            self._model = load_sentence_transformer(**base_kwargs, local_files_only=True)
        elif local_files_setting in {"0", "false", "no"}:
            self._model = load_sentence_transformer(**base_kwargs)
        else:
            try:
                self._model = load_sentence_transformer(**base_kwargs, local_files_only=True)
            except Exception:
                self._model = load_sentence_transformer(**base_kwargs)
        MODEL_CACHE[self.model_name] = self._model
        return self._model

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        texts = list(texts)
        if not texts:
            return []
        embeddings = self._load_model().encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embeddings.tolist()

    def __call__(self, input: list[str]) -> list[list[float]]:
        return self._embed_texts(list(input))

    def embed_query(self, input: list[str]) -> list[list[float]]:
        return self._embed_texts(list(input))

    def embed_documents(self, input: list[str]) -> list[list[float]]:
        return self._embed_texts(list(input))


def get_chroma_client(path: Path | None = None):
    try:
        import chromadb
    except ImportError as exc:
        raise ChromaStoreError(
            "chromadb is not installed. Install advanced dependencies with: "
            "pip install -r requirements-advanced.txt"
        ) from exc
    return chromadb.PersistentClient(path=str(path or get_chroma_path()))


def get_chroma_collection(
    *,
    create: bool = True,
    reset: bool = False,
    path: Path | None = None,
    collection_name: str | None = None,
    embedding_model: str | None = None,
):
    client = get_chroma_client(path)
    name = collection_name or get_chroma_collection_name()
    embedding_function = SentenceTransformerEmbeddingFunction(embedding_model)

    if reset:
        try:
            client.delete_collection(name)
        except Exception:
            pass

    if create:
        return client.get_or_create_collection(
            name=name,
            embedding_function=embedding_function,
            metadata={"description": "GeoAI RAG knowledge base"},
        )
    try:
        return client.get_collection(name=name, embedding_function=embedding_function)
    except Exception as exc:
        raise ChromaStoreError(
            f"Chroma collection '{name}' does not exist at '{path or get_chroma_path()}'. "
            "Run: python scripts/build_chroma_store.py"
        ) from exc


def normalize_metadata(metadata: dict[str, Any] | None) -> dict[str, str | int | float | bool]:
    normalized = {}
    for key, value in (metadata or {}).items():
        if isinstance(value, (str, int, float, bool)):
            normalized[key] = value
        elif value is not None:
            normalized[key] = str(value)
    return normalized


def chroma_domain_boost(query: str, text: str, metadata: dict[str, Any]) -> float:
    query_lower = query.lower()
    text_lower = text.lower()
    source = str(metadata.get("source", "")).lower()
    doc_type = metadata.get("type")
    boost = 0.0

    road_terms = ("道路", "路网", "road", "roads", "length", "长度", "里程")
    unsupported_terms = ("学校", "建筑", "building", "school")

    if any(term in query_lower for term in road_terms):
        if "sum_line_lengths" in text_lower or "native:sumlinelengths" in text_lower:
            boost += 0.18
        if "road length" in text_lower or "road_length" in text_lower:
            boost += 0.14
        if doc_type == "workflow_example":
            boost += 0.10
        if "task_guides" in source:
            boost += 0.08
        if doc_type == "eval_case" and any(term in text_lower for term in unsupported_terms):
            boost -= 0.10

    return boost


def build_chroma_store(
    documents: list[dict[str, Any]],
    *,
    reset: bool = True,
    path: Path | None = None,
    collection_name: str | None = None,
    embedding_model: str | None = None,
) -> dict[str, Any]:
    collection = get_chroma_collection(
        create=True,
        reset=reset,
        path=path,
        collection_name=collection_name,
        embedding_model=embedding_model,
    )

    ids = [document["id"] for document in documents]
    texts = [document["text"] for document in documents]
    metadatas = [normalize_metadata(document.get("metadata", {})) for document in documents]
    if ids:
        collection.add(ids=ids, documents=texts, metadatas=metadatas)

    return {
        "path": str(path or get_chroma_path()),
        "collection": collection_name or get_chroma_collection_name(),
        "embedding_model": get_embedding_model_name(embedding_model),
        "documents": len(documents),
    }


def retrieve_chroma_context(
    query: str,
    top_k: int = 4,
    *,
    path: Path | None = None,
    collection_name: str | None = None,
    embedding_model: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    collection = get_chroma_collection(
        create=False,
        path=path,
        collection_name=collection_name,
        embedding_model=embedding_model,
    )
    response = collection.query(
        query_texts=[query],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    ids = response.get("ids", [[]])[0]
    documents = response.get("documents", [[]])[0]
    metadatas = response.get("metadatas", [[]])[0]
    distances = response.get("distances", [[]])[0]

    results = []
    for doc_id, text, metadata, distance in zip(ids, documents, metadatas, distances):
        similarity = 1 / (1 + float(distance))
        score = similarity + chroma_domain_boost(query, text, metadata or {})
        results.append({
            "id": doc_id,
            "score": round(score, 4),
            "similarity": round(similarity, 4),
            "distance": round(float(distance), 4),
            "text": text,
            "metadata": metadata or {},
            "retriever": "chroma",
        })
    results.sort(key=lambda item: item["score"], reverse=True)

    context_parts = []
    for index, result in enumerate(results, start=1):
        source = result.get("metadata", {}).get("source", "unknown")
        context_parts.append(
            f"[Retrieved {index} | score={result['score']} | source={source}]\n{result['text']}"
        )
    return "\n\n".join(context_parts), results


def retrieve_chroma_context_with_rerank(
    query: str,
    top_k: int = 4,
    *,
    candidate_k: int | None = None,
    path: Path | None = None,
    collection_name: str | None = None,
    embedding_model: str | None = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    from .config import env_int
    from .reranker import build_context, rerank_documents

    dense_k = candidate_k or env_int("RETRIEVAL_TOP_K", 20)
    _, candidates = retrieve_chroma_context(
        query,
        top_k=dense_k,
        path=path,
        collection_name=collection_name,
        embedding_model=embedding_model,
    )
    ranked, metadata = rerank_documents(query, candidates, top_k=top_k)
    return build_context(ranked), ranked, metadata
