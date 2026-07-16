from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
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
    layer_target = CACHE_ROOT / f"{key}.gpkg"
    layer_temp = CACHE_ROOT / f".{key}.{uuid.uuid4().hex}.gpkg.tmp"
    shutil.copy2(source, layer_temp)
    os.replace(layer_temp, layer_target)
    store_cached_json(key, metadata)


def restore_cached_json(key: str) -> Any | None:
    path = CACHE_ROOT / f"{key}.json"
    if not cache_enabled() or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def store_cached_json(key: str, payload: Any) -> None:
    if not cache_enabled():
        return
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    target = CACHE_ROOT / f"{key}.json"
    temp = CACHE_ROOT / f".{key}.{uuid.uuid4().hex}.json.tmp"
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temp, target)
