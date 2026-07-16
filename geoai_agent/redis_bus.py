from __future__ import annotations

import json
import os
import threading
from typing import Any


_CLIENT: Any | None = None
_LOCK = threading.Lock()
_PREFIX = "geoai:final"


def redis_url() -> str:
    return os.getenv("REDIS_URL", "").strip()


def redis_enabled() -> bool:
    return bool(redis_url())


def redis_required() -> bool:
    return os.getenv("REDIS_REQUIRED", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _client():
    global _CLIENT
    if not redis_enabled():
        return None
    with _LOCK:
        if _CLIENT is None:
            try:
                import redis
            except ImportError as exc:
                raise RuntimeError(
                    "REDIS_URL is configured but the redis package is not installed."
                ) from exc
            _CLIENT = redis.Redis.from_url(
                redis_url(),
                decode_responses=True,
                socket_connect_timeout=1.5,
                socket_timeout=2.0,
                health_check_interval=15,
            )
    return _CLIENT


def check_redis() -> dict[str, Any]:
    if not redis_enabled():
        return {
            "configured": False,
            "available": False,
            "required": False,
            "detail": "REDIS_URL is not configured; file and MySQL fallbacks are active.",
        }
    try:
        client = _client()
        assert client is not None
        client.ping()
        return {
            "configured": True,
            "available": True,
            "required": redis_required(),
            "detail": "Redis is reachable.",
        }
    except Exception as exc:
        return {
            "configured": True,
            "available": False,
            "required": redis_required(),
            "detail": f"Redis unavailable; durable fallbacks remain active: {type(exc).__name__}: {exc}",
        }


def publish_task_event(task_id: str, event: dict[str, Any]) -> bool:
    """Mirror a durable file event to a bounded Redis Stream.

    The JSONL task trace remains the source of truth. Redis only accelerates cross-process
    observation and can be lost without losing the task or its audit trail.
    """
    if not redis_enabled():
        return False
    try:
        client = _client()
        assert client is not None
        key = f"{_PREFIX}:task:{task_id}:events"
        client.xadd(
            key,
            {"payload": json.dumps(event, ensure_ascii=False, default=str)},
            maxlen=2000,
            approximate=True,
        )
        client.expire(key, int(os.getenv("REDIS_EVENT_TTL_SECONDS", "86400")))
        return True
    except Exception:
        return False


def write_worker_lease(worker_id: str, payload: dict[str, Any], ttl_seconds: int) -> bool:
    if not redis_enabled():
        return False
    try:
        client = _client()
        assert client is not None
        key = f"{_PREFIX}:worker:{worker_id}"
        client.setex(key, ttl_seconds, json.dumps(payload, ensure_ascii=False, default=str))
        return True
    except Exception:
        return False


def read_worker_leases() -> list[dict[str, Any]]:
    if not redis_enabled():
        return []
    try:
        client = _client()
        assert client is not None
        payloads: list[dict[str, Any]] = []
        for key in client.scan_iter(match=f"{_PREFIX}:worker:*", count=20):
            raw = client.get(key)
            if raw:
                item = json.loads(raw)
                item["heartbeat_backend"] = "redis_lease"
                payloads.append(item)
        return payloads
    except Exception:
        return []


def cache_get_json(key: str) -> dict[str, Any] | None:
    if not redis_enabled():
        return None
    try:
        client = _client()
        assert client is not None
        raw = client.get(f"{_PREFIX}:cache:{key}")
        return json.loads(raw) if raw else None
    except Exception:
        return None


def cache_set_json(key: str, value: dict[str, Any], ttl_seconds: int = 600) -> bool:
    if not redis_enabled():
        return False
    try:
        client = _client()
        assert client is not None
        client.setex(
            f"{_PREFIX}:cache:{key}",
            ttl_seconds,
            json.dumps(value, ensure_ascii=False, default=str),
        )
        return True
    except Exception:
        return False
