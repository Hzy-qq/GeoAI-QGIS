from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from .config import env_int, env_str
from .dataset_catalog import validate_catalog_url
from .errors import PermanentError, TransientError


def request_json(
    url: str,
    *,
    query: dict[str, str] | None = None,
    form: dict[str, str] | None = None,
    timeout: int | None = None,
    retries: int | None = None,
) -> Any:
    validate_catalog_url(url)
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
                return json.loads(payload.decode("utf-8"))
        except error.HTTPError as exc:
            details = exc.read(1000).decode("utf-8", errors="replace")
            if exc.code in {408, 429, 500, 502, 503, 504}:
                last_error = TransientError(f"HTTP {exc.code}: {details}")
            else:
                raise PermanentError(f"HTTP {exc.code}: {details}") from exc
        except (error.URLError, TimeoutError) as exc:
            last_error = TransientError(f"Data request failed: {exc}")
        except json.JSONDecodeError as exc:
            raise PermanentError("Data source returned invalid JSON.") from exc
        if attempt < retry_count:
            time.sleep(min(2 ** attempt, 4))
    raise last_error or TransientError("Data request failed.")


def download_file(
    url: str,
    output: Path,
    *,
    timeout: int | None = None,
    retries: int | None = None,
) -> Path:
    validate_catalog_url(url)
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
            os.replace(temp_path, output)
            return output
        except error.HTTPError as exc:
            if exc.code in {408, 429, 500, 502, 503, 504}:
                last_error = TransientError(f"HTTP {exc.code} while downloading {url}")
            else:
                raise PermanentError(f"HTTP {exc.code} while downloading {url}") from exc
        except (error.URLError, TimeoutError) as exc:
            last_error = TransientError(f"File download failed: {exc}")
        finally:
            if temp_path.exists() and not output.exists():
                temp_path.unlink(missing_ok=True)
        if attempt < retry_count:
            time.sleep(min(2 ** attempt, 4))
    raise last_error or TransientError(f"File download failed: {url}")
