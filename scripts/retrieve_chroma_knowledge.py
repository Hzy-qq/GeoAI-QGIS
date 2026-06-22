from pathlib import Path
import os
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from geoai_agent.chroma_store import ChromaStoreError, retrieve_chroma_context


def main() -> None:
    os.chdir(PROJECT_ROOT)
    query = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else input("请输入检索问题：")
    try:
        context, results = retrieve_chroma_context(query, top_k=4)
    except ChromaStoreError as exc:
        print("Chroma retrieval failed:", exc)
        raise SystemExit(1) from exc

    print("Retrieved docs:", len(results))
    print()
    print(context)


if __name__ == "__main__":
    main()
