from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geoai_agent.chroma_store import retrieve_chroma_context_with_rerank


def main() -> None:
    cases = json.loads((PROJECT_ROOT / "evals" / "retrieval_eval_cases.json").read_text(encoding="utf-8"))
    recalls, reciprocal_ranks = [], []
    for case in cases:
        _, docs, metadata = retrieve_chroma_context_with_rerank(case["query"], top_k=4)
        ids = [item["id"] for item in docs]
        relevant = set(case["relevant_ids"])
        hit_count = len(relevant.intersection(ids))
        recall = hit_count / len(relevant)
        rank = next((index for index, value in enumerate(ids, start=1) if value in relevant), None)
        rr = 1 / rank if rank else 0
        recalls.append(recall)
        reciprocal_ranks.append(rr)
        print(f"[{case['id']}] recall@4={recall:.2f} rr={rr:.2f} ids={ids}")
    print(f"Mean recall@4={sum(recalls)/len(recalls):.3f}")
    print(f"MRR={sum(reciprocal_ranks)/len(reciprocal_ranks):.3f}")


if __name__ == "__main__":
    main()
