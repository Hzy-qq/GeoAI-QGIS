from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = PROJECT_ROOT / "knowledge"


MARKDOWN_DOCUMENT_TYPES = {
    "qgis_tools": "tool_doc",
    "task_guides": "task_guide",
}


def chunk_markdown(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    chunks = []
    current_title = path.stem
    current_id = None
    current_lines = []

    def flush() -> None:
        if current_lines:
            chunks.append({
                "id": current_id or f"{path.stem}:{len(chunks) + 1}",
                "text": "\n".join(current_lines).strip(),
                "metadata": {
                    "source": str(path.relative_to(PROJECT_ROOT)),
                    "title": current_title,
                    "type": MARKDOWN_DOCUMENT_TYPES.get(path.stem, "knowledge_doc"),
                },
            })

    for line in text.splitlines():
        if line.startswith("#"):
            flush()
            heading = line.lstrip("#").strip() or path.stem
            current_id = None
            if heading.endswith("}") and "{#" in heading:
                heading, raw_id = heading.rsplit("{#", 1)
                current_id = raw_id[:-1].strip()
            current_title = heading.strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    flush()
    return chunks


def load_workflow_examples(path: Path = KNOWLEDGE_DIR / "workflow_examples.jsonl") -> list[dict[str, Any]]:
    if not path.exists():
        return []
    chunks = []
    with open(path, "r", encoding="utf-8") as f:
        for index, line in enumerate(f, start=1):
            if not line.strip():
                continue
            data = json.loads(line)
            chunks.append({
                "id": data.get("id") or f"workflow_example:{index}",
                "text": json.dumps(data, ensure_ascii=False, indent=2),
                "metadata": {"source": str(path.relative_to(PROJECT_ROOT)), "type": "workflow_example"},
            })
    return chunks


def validate_knowledge_documents(documents: list[dict[str, Any]]) -> None:
    """Prevent evaluation data from leaking into the production knowledge base."""
    for document in documents:
        metadata = document.get("metadata", {})
        source = str(metadata.get("source", "")).replace("\\", "/")
        if metadata.get("type") == "eval_case" or source.startswith("evals/"):
            raise ValueError(
                f"Evaluation data must not be indexed as knowledge: {document.get('id')}"
            )


def load_knowledge_documents() -> list[dict[str, Any]]:
    documents = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        documents.extend(chunk_markdown(path))
    documents.extend(load_workflow_examples())
    validate_knowledge_documents(documents)
    return documents
