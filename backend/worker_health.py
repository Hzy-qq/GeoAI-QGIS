from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from geoai_agent.config import PROJECT_ROOT, env_float, env_str
from geoai_agent.redis_bus import read_worker_leases, write_worker_lease


def heartbeat_path() -> Path:
    configured = Path(
        env_str("WORKER_HEARTBEAT_PATH", "outputs/worker_heartbeat.json")
    )
    return configured if configured.is_absolute() else PROJECT_ROOT / configured


def write_worker_heartbeat(
    worker_id: str,
    state: str,
    *,
    current_task_id: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    payload = {
        "worker_id": worker_id,
        "pid": os.getpid(),
        "state": state,
        "current_task_id": current_task_id,
        "detail": detail,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    target = heartbeat_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, target)
    ttl_seconds = max(
        3,
        int(env_float("WORKER_HEARTBEAT_TIMEOUT_SECONDS", 10.0) * 2),
    )
    write_worker_lease(worker_id, payload, ttl_seconds)
    return payload


def read_worker_health(timeout_seconds: float | None = None) -> dict[str, Any]:
    timeout = timeout_seconds or env_float("WORKER_HEARTBEAT_TIMEOUT_SECONDS", 10.0)
    leases = read_worker_leases()
    if leases:
        preferred = next(
            (item for item in leases if item.get("state") in {"running", "idle", "starting"}),
            leases[0],
        )
        preferred.update(
            {
                "active": preferred.get("state") not in {"stopped", "missing"},
                "ready": preferred.get("state") in {"starting", "idle", "running"},
                "age_seconds": 0.0,
            }
        )
        return preferred
    target = heartbeat_path()
    if not target.exists():
        return {
            "active": False,
            "ready": False,
            "state": "missing",
            "detail": "No Worker heartbeat has been recorded.",
        }
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        updated_at = datetime.fromisoformat(str(payload["updated_at"]))
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age_seconds = max(
            0.0,
            (datetime.now(timezone.utc) - updated_at).total_seconds(),
        )
        state = str(payload.get("state") or "unknown")
        active = age_seconds <= timeout and state not in {"stopped", "missing"}
        payload.update(
            {
                "active": active,
                "ready": active and state in {"starting", "idle", "running"},
                "age_seconds": round(age_seconds, 2),
                "heartbeat_backend": "local_file_fallback",
            }
        )
        if not active:
            payload["detail"] = (
                f"Worker heartbeat is stale ({age_seconds:.1f}s old). "
                "Restart the API/Worker."
            )
        return payload
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return {
            "active": False,
            "ready": False,
            "state": "invalid",
            "detail": f"Could not read Worker heartbeat: {exc}",
        }
