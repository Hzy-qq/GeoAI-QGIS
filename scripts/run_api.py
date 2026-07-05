from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from geoai_agent.config import env_int, env_str


def main() -> None:
    import uvicorn

    uvicorn.run(
        "backend.api:app",
        host=env_str("API_HOST", "127.0.0.1"),
        port=env_int("API_PORT", 8000),
        reload=False,
    )


if __name__ == "__main__":
    main()

