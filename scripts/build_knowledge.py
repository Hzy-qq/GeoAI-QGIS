from __future__ import annotations

import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geoai_agent.chroma_store import ChromaStoreError, build_chroma_store
from geoai_agent.knowledge_loader import load_knowledge_documents
from geoai_agent.versioning import KNOWLEDGE_BASE_VERSION


def main() -> None:
    os.chdir(PROJECT_ROOT)
    try:
        result = build_chroma_store(load_knowledge_documents(), reset=True)
    except ChromaStoreError as exc:
        raise SystemExit(f"Chroma store build failed: {exc}") from exc
    result["knowledge_base_version"] = KNOWLEDGE_BASE_VERSION
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

