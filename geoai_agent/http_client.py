from __future__ import annotations

import json
import os
import random
import time
import http.client
import socket
import ssl
import threading
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from .config import env_int, env_str
from .dataset_catalog import validate_catalog_url
from .errors import PermanentError, TransientError


_ENDPOINT_LOCK = threading.Lock()
_ENDPOINT_STATE: dict[str, dict[str, float]] = {}


def _endpoint_key(url: str) -> str:
    parsed = parse.urlparse(url)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def endpoint_available(url: str) -> bool:
    key = _endpoint_key(url)
    with _ENDPOINT_LOCK:
        state = _ENDPOINT_STATE.get(key, {})
        return time.monotonic() >= float(state.get("open_until", 0.0))


def _record_endpoint_success(url: str) -> None:
    with _ENDPOINT_LOCK:
        _ENDPOINT_STATE.pop(_endpoint_key(url), None)


def _record_endpoint_failure(url: str) -> None:
    threshold = max(1, env_int("DATA_CIRCUIT_FAILURE_THRESHOLD", 2))
    cooldown = max(1, env_int("DATA_CIRCUIT_COOLDOWN_SECONDS", 60))
    key = _endpoint_key(url)
    with _ENDPOINT_LOCK:
        state = _ENDPOINT_STATE.setdefault(key, {"failures": 0.0, "open_until": 0.0})
        state["failures"] = float(state.get("failures", 0.0)) + 1.0
        if state["failures"] >= threshold:
            state["open_until"] = time.monotonic() + cooldown


def reset_endpoint_health() -> None:
    """Test/operations hook for clearing in-process circuit breaker state."""
    with _ENDPOINT_LOCK:
        _ENDPOINT_STATE.clear()


def _backoff(attempt: int) -> None:
    base = min(2 ** attempt, max(1, env_int("DATA_HTTP_MAX_BACKOFF_SECONDS", 8)))
    jitter_ms = max(0, env_int("DATA_HTTP_JITTER_MS", 250))
    time.sleep(base + random.uniform(0.0, jitter_ms / 1000.0))


def request_json(
    url: str,
    *,
    query: dict[str, str] | None = None,
    form: dict[str, str] | None = None,
    timeout: int | None = None,
    retries: int | None = None,
) -> Any:
    validate_catalog_url(url)
    if not endpoint_available(url):
        raise TransientError(f"Endpoint circuit is temporarily open: {_endpoint_key(url)}")
    timeout_value = timeout or env_int("DATA_HTTP_TIMEOUT_SECONDS", 120)
    retry_count = retries if retries is not None else env_int("DATA_HTTP_RETRIES", 2)
    max_bytes = env_int("DATA_MAX_RESPONSE_BYTES", 100_000_000)
    if query:
        url = f"{url}?{parse.urlencode(query)}"
    body = parse.urlencode(form).encode("utf-8") if form else None
    headers = {
        "User-Agent": env_str(
            "OSM_USER_AGENT",
            "GeoAI-QGIS-State3/0.5 (educational GIS agent)",
        ),
        "Accept": "application/json",
    }
    if body:
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    last_error: BaseException | None = None
    for attempt in range(retry_count + 1):
        try:
            api_request = request.Request(url, data=body, headers=headers)
            with request.urlopen(api_request, timeout=timeout_value) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > max_bytes:
                    raise PermanentError("Data response exceeds configured size limit.")
                payload = response.read(max_bytes + 1)
                if len(payload) > max_bytes:
                    raise PermanentError("Data response exceeds configured size limit.")
                if length and len(payload) != int(length):
                    raise TransientError(
                        f"Incomplete response: expected {length} bytes, received {len(payload)}."
                    )
                result = json.loads(payload.decode("utf-8"))
                _record_endpoint_success(url)
                return result
        except TransientError as exc:
            last_error = exc
        except error.HTTPError as exc:
            details = exc.read(1000).decode("utf-8", errors="replace")
            if exc.code in {408, 429, 500, 502, 503, 504}:
                last_error = TransientError(f"HTTP {exc.code}: {details}")
            else:
                raise PermanentError(f"HTTP {exc.code}: {details}") from exc
        except (
            error.URLError,
            TimeoutError,
            ConnectionError,
            http.client.HTTPException,
            socket.timeout,
            ssl.SSLError,
        ) as exc:
            last_error = TransientError(f"Data request failed: {exc}")
        except json.JSONDecodeError as exc:
            raise PermanentError("Data source returned invalid JSON.") from exc
        if attempt < retry_count:
            _backoff(attempt)
    _record_endpoint_failure(url)
    raise last_error or TransientError("Data request failed.")


def download_file(
    url: str,
    output: Path,
    *,
    timeout: int | None = None,
    retries: int | None = None,
) -> Path:
    validate_catalog_url(url)
    if not endpoint_available(url):
        raise TransientError(f"Endpoint circuit is temporarily open: {_endpoint_key(url)}")
    timeout_value = timeout or env_int("DATA_FILE_TIMEOUT_SECONDS", 300)
    retry_count = retries if retries is not None else env_int("DATA_HTTP_RETRIES", 2)
    max_bytes = env_int("DATA_MAX_FILE_BYTES", 250_000_000)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output.with_suffix(output.suffix + ".part")
    last_error: BaseException | None = None
    for attempt in range(retry_count + 1):
        try:
            api_request = request.Request(
                url,
                headers={"User-Agent": env_str("OSM_USER_AGENT", "GeoAI-QGIS-State3/0.5")},
            )
            with request.urlopen(api_request, timeout=timeout_value) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > max_bytes:
                    raise PermanentError("Download exceeds configured file size limit.")
                written = 0
                with temp_path.open("wb") as stream:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > max_bytes:
                            raise PermanentError("Download exceeds configured file size limit.")
                        stream.write(chunk)
                if length and written != int(length):
                    raise TransientError(
                        f"Incomplete download from {url}: expected {length} bytes, "
                        f"received {written}."
                    )
            os.replace(temp_path, output)
            _record_endpoint_success(url)
            return output
        except TransientError as exc:
            last_error = exc
        except error.HTTPError as exc:
            if exc.code in {408, 429, 500, 502, 503, 504}:
                last_error = TransientError(f"HTTP {exc.code} while downloading {url}")
            else:
                raise PermanentError(f"HTTP {exc.code} while downloading {url}") from exc
        except (
            error.URLError,
            TimeoutError,
            ConnectionError,
            http.client.HTTPException,
            socket.timeout,
            ssl.SSLError,
        ) as exc:
            last_error = TransientError(f"File download failed: {exc}")
        finally:
            temp_path.unlink(missing_ok=True)
        if attempt < retry_count:
            _backoff(attempt)
    _record_endpoint_failure(url)
    raise last_error or TransientError(f"File download failed: {url}")
