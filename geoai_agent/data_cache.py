from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT, env_bool


CACHE_ROOT = PROJECT_ROOT / "outputs" / "data_cache"


def cache_enabled() -> bool:
    return env_bool("DATA_CACHE_ENABLED", True)


def cache_key(kind: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
    return f"{kind}-{digest}"


def restore_cached_layer(key: str, output: Path) -> bool:
    source = CACHE_ROOT / f"{key}.gpkg"
    if not cache_enabled() or not source.exists():
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)
    return True


def store_cached_layer(key: str, source: Path, metadata: dict[str, Any]) -> None:
    if not cache_enabled():
        return
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, CACHE_ROOT / f"{key}.gpkg")
    (CACHE_ROOT / f"{key}.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
