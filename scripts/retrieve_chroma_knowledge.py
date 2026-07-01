from pathlib import Path
import os
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geoai_agent.chroma_store import retrieve_chroma_context_with_rerank


def main() -> None:
    os.chdir(PROJECT_ROOT)
    query = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else input("请输入检索问题：")
    context, results, metadata = retrieve_chroma_context_with_rerank(query, top_k=4)
    print("Rerank metadata:", metadata)
    for index, item in enumerate(results, start=1):
        print(f"[{index}] {item['id']} score={item.get('rerank_score', item.get('score'))}")
    print("\nContext:\n", context)


if __name__ == "__main__":
    main()
