from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geoai_agent.config import env_int, env_str


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the GeoAI API and local companion Worker.")
    parser.add_argument(
        "--api-only",
        action="store_true",
        help="Do not launch a companion Worker (for Docker or separately managed Workers).",
    )
    parser.add_argument("--worker-poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    worker: subprocess.Popen | None = None
    if not args.api_only:
        command = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "run_worker.py"),
            "--poll-seconds",
            str(max(0.2, args.worker_poll_seconds)),
        ]
        worker = subprocess.Popen(command, cwd=PROJECT_ROOT)
        print(f"Started companion GIS Worker (PID {worker.pid}).", flush=True)
    try:
        uvicorn.run(
            "backend.api:app",
            host=env_str("API_HOST", "127.0.0.1"),
            port=env_int("API_PORT", 8000),
            reload=False,
        )
    finally:
        if worker is not None and worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)


if __name__ == "__main__":
    main()
