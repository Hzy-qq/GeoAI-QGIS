from pathlib import Path
import json
import os
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from geoai_agent.chroma_store import ChromaStoreError, build_chroma_store
from geoai_agent.knowledge_loader import load_knowledge_documents


def main() -> None:
    os.chdir(PROJECT_ROOT)
    documents = load_knowledge_documents()
    try:
        result = build_chroma_store(documents, reset=True)
    except ChromaStoreError as exc:
        print("Chroma store build failed:", exc)
        raise SystemExit(1) from exc

    print("Chroma vector store built:")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
