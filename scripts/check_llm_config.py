from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from geoai_agent.llm_client import get_llm_api_key, get_llm_base_url, get_llm_model, get_llm_provider


def mask_key(api_key: str) -> str:
    if len(api_key) <= 12:
        return "***"
    return f"{api_key[:7]}...{api_key[-4:]}"


def main() -> None:
    api_key = get_llm_api_key()
    print("LLM config found.")
    print("Provider:", get_llm_provider())
    print("API key:", mask_key(api_key))
    print("Model:", get_llm_model())
    print("Base URL:", get_llm_base_url())


if __name__ == "__main__":
    main()
