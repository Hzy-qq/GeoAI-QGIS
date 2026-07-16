from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.worker import TaskWorker


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the GeoAI task worker.")
    parser.add_argument("--once", action="store_true", help="Process at most one queued task.")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    worker = TaskWorker()
    if args.once:
        try:
            worker.prepare()
            processed = worker.run_once()
            print("Processed one task." if processed else "No pending task.")
        finally:
            worker.close()
        return
    worker.run_forever(max(0.2, args.poll_seconds))


if __name__ == "__main__":
    main()
