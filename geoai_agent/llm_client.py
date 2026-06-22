from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib import error, request


DEFAULT_LLM_PROVIDER = "deepseek"
DEFAULT_LLM_BASE_URL = "https://api.deepseek.com"
DEFAULT_LLM_MODEL = "deepseek-v4-flash"
DEEPSEEK_MODELS = {"deepseek-v4-flash", "deepseek-v4-pro"}
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"


class LLMClientError(RuntimeError):
    """Raised when the LLM API call or response parsing fails."""


def load_dotenv(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _first_env_value(names: list[str]) -> str | None:
    load_dotenv()
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def get_llm_provider() -> str:
    return _first_env_value(["LLM_PROVIDER"]) or DEFAULT_LLM_PROVIDER


def get_llm_api_key() -> str:
    api_key = _first_env_value(["LLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"])
    if not api_key:
        raise LLMClientError(
            "LLM_API_KEY is not set. Set it in your shell or in a local .env file."
        )
    return api_key


def get_llm_model(model: str | None = None) -> str:
    selected_model = model or _first_env_value(["LLM_MODEL", "DEEPSEEK_MODEL"])
    if not selected_model and get_llm_provider().lower() == "openai":
        selected_model = _first_env_value(["OPENAI_MODEL"]) or "gpt-5.5"

    selected_model = selected_model or DEFAULT_LLM_MODEL
    if get_llm_provider().lower() == "deepseek" and selected_model not in DEEPSEEK_MODELS:
        raise LLMClientError(
            "Invalid DeepSeek model name: "
            f"{selected_model}. Use one of: {', '.join(sorted(DEEPSEEK_MODELS))}."
        )
    return selected_model


def get_llm_base_url() -> str:
    base_url = _first_env_value(["LLM_BASE_URL", "DEEPSEEK_BASE_URL"])
    if base_url:
        return base_url.rstrip("/")
    if get_llm_provider().lower() == "openai":
        return _first_env_value(["OPENAI_BASE_URL"]) or "https://api.openai.com/v1"
    return DEFAULT_LLM_BASE_URL


def _messages_with_schema(
    input_messages: list[dict[str, str]],
    json_schema: dict[str, Any],
) -> list[dict[str, str]]:
    schema_text = json.dumps(json_schema, ensure_ascii=False, indent=2)
    schema_instruction = (
        "\n\nYou must return one valid JSON object only. Do not wrap it in Markdown. "
        "The JSON object must match this JSON Schema:\n"
        f"{schema_text}"
    )

    messages = [dict(message) for message in input_messages]
    for message in messages:
        if message.get("role") == "system":
            message["content"] = message.get("content", "") + schema_instruction
            return messages
    return [{"role": "system", "content": schema_instruction.strip()}, *messages]


def _extract_chat_content(response_data: dict[str, Any]) -> str:
    choices = response_data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMClientError("Could not find choices in LLM response.")

    message = choices[0].get("message", {})
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    raise LLMClientError("Could not find message content in LLM response.")


def _parse_json_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMClientError(f"Model output was not valid JSON: {text}") from exc

    if not isinstance(data, dict):
        raise LLMClientError("Model output JSON must be an object.")
    return data


def create_json_response(
    input_messages: list[dict[str, str]],
    json_schema: dict[str, Any],
    *,
    model: str | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": get_llm_model(model),
        "messages": _messages_with_schema(input_messages, json_schema),
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    if temperature is not None:
        payload["temperature"] = temperature

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    api_request = request.Request(
        f"{get_llm_base_url()}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {get_llm_api_key()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(api_request, timeout=60) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise LLMClientError(f"LLM API HTTP error {exc.code}: {details}") from exc
    except error.URLError as exc:
        raise LLMClientError(f"LLM API request failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LLMClientError("LLM API returned invalid JSON.") from exc

    return _parse_json_text(_extract_chat_content(response_data))


def create_text_response(
    input_messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float | None = None,
) -> str:
    payload: dict[str, Any] = {
        "model": get_llm_model(model),
        "messages": input_messages,
        "stream": False,
    }
    if temperature is not None:
        payload["temperature"] = temperature

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    api_request = request.Request(
        f"{get_llm_base_url()}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {get_llm_api_key()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(api_request, timeout=60) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise LLMClientError(f"LLM API HTTP error {exc.code}: {details}") from exc
    except error.URLError as exc:
        raise LLMClientError(f"LLM API request failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LLMClientError("LLM API returned invalid JSON.") from exc

    return _extract_chat_content(response_data).strip()
