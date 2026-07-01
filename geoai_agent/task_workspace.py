from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import PROJECT_ROOT
from .errors import PermanentError


WORKSPACE_URI_PREFIX = "workspace://"
SAFE_TASK_ID = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")


@dataclass(frozen=True)
class TaskWorkspace:
    task_id: str
    root: Path

    @classmethod
    def create(cls, task_id: str | None = None) -> "TaskWorkspace":
        value = task_id or uuid.uuid4().hex[:16]
        if not SAFE_TASK_ID.fullmatch(value):
            raise PermanentError("task_id contains unsafe characters.")
        root = (PROJECT_ROOT / "outputs" / "tasks" / value).resolve()
        for name in ("raw", "processed", "result", "trace"):
            (root / name).mkdir(parents=True, exist_ok=True)
        return cls(task_id=value, root=root)

    def resolve(self, value: str | Path) -> Path:
        raw = str(value)
        if raw.startswith(WORKSPACE_URI_PREFIX):
            candidate = self.root / raw[len(WORKSPACE_URI_PREFIX):]
        else:
            path = Path(raw)
            candidate = path if path.is_absolute() else PROJECT_ROOT / path
        resolved = candidate.resolve()
        allowed_roots = (self.root.resolve(), PROJECT_ROOT.resolve())
        if not any(resolved == root or root in resolved.parents for root in allowed_roots):
            raise PermanentError(f"Path escapes the project workspace: {value}")
        return resolved

    def resolve_params(self, params: dict) -> dict:
        resolved = {}
        path_keys = {
            "INPUT", "OUTPUT", "OVERLAY", "POLYGONS", "LINES", "POINTS",
            "TARGET", "REFERENCE", "AREA",
            "BOUNDARY",
        }
        for key, value in params.items():
            if key in path_keys and isinstance(value, str):
                resolved[key] = str(self.resolve(value))
            else:
                resolved[key] = value
        return resolved
