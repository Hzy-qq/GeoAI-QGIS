from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .task_workspace import TaskWorkspace
from .redis_bus import publish_task_event


def progress_path(task_id: str) -> Path:
    workspace = TaskWorkspace.create(task_id)
    path = workspace.root / "trace" / "progress.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def append_progress(task_id: str, event: dict[str, Any]) -> None:
    path = progress_path(task_id)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    publish_task_event(task_id, event)


def read_progress(task_id: str, start: int = 0) -> tuple[list[dict[str, Any]], int]:
    path = progress_path(task_id)
    if not path.exists():
        return [], start
    lines = path.read_text(encoding="utf-8").splitlines()
    events: list[dict[str, Any]] = []
    for line in lines[start:]:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events, len(lines)
