from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geoai_agent.config import load_dotenv
from geoai_agent.qgis_runner import get_qgis_process_cmd


def main() -> None:
    load_dotenv()
    checks = {
        "geopandas": importlib.util.find_spec("geopandas") is not None,
        "chromadb": importlib.util.find_spec("chromadb") is not None,
        "sentence_transformers": importlib.util.find_spec("sentence_transformers") is not None,
        "langgraph": importlib.util.find_spec("langgraph") is not None,
        "LLM_API_KEY": bool(os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")),
        "QGIS_PROCESS_CMD": Path(get_qgis_process_cmd()).exists(),
    }
    for name, passed in checks.items():
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
