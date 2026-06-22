from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = PROJECT_ROOT / "knowledge"
EVAL_CASES_PATH = PROJECT_ROOT / "evals" / "eval_cases.json"


def chunk_markdown(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    chunks = []
    current_title = path.stem
    current_lines = []

    def flush() -> None:
        if current_lines:
            chunks.append({
                "id": f"{path.stem}:{len(chunks) + 1}",
                "text": "\n".join(current_lines).strip(),
                "metadata": {"source": str(path.relative_to(PROJECT_ROOT)), "title": current_title},
            })

    for line in text.splitlines():
        if line.startswith("#"):
            flush()
            current_title = line.lstrip("#").strip() or path.stem
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
                "id": f"workflow_example:{index}",
                "text": json.dumps(data, ensure_ascii=False, indent=2),
                "metadata": {"source": str(path.relative_to(PROJECT_ROOT)), "type": "workflow_example"},
            })
    return chunks


def load_eval_cases(path: Path = EVAL_CASES_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    cases = json.loads(path.read_text(encoding="utf-8"))
    chunks = []
    for case in cases:
        chunks.append({
            "id": f"eval_case:{case.get('id', len(chunks) + 1)}",
            "text": json.dumps(case, ensure_ascii=False, indent=2),
            "metadata": {"source": str(path.relative_to(PROJECT_ROOT)), "type": "eval_case"},
        })
    return chunks


def load_knowledge_documents() -> list[dict[str, Any]]:
    documents = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        documents.extend(chunk_markdown(path))
    documents.extend(load_workflow_examples())
    documents.extend(load_eval_cases())
    return documents
