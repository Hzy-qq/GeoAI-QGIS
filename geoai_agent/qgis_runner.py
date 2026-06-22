import os
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QGIS_PROCESS_CMD = r"F:\QGIS\bin\qgis_process-qgis-ltr.bat"


def get_qgis_process_cmd() -> str:
    return os.getenv("QGIS_PROCESS_CMD", DEFAULT_QGIS_PROCESS_CMD)


def delete_existing_file(path: str):
    p = PROJECT_ROOT / path if not Path(path).is_absolute() else Path(path)
    if p.exists():
        if p.is_file():
            p.unlink()
        else:
            raise ValueError(f"Output path exists but is not a file: {p}")


def run_qgis_algorithm(algorithm_id: str, params: dict, overwrite: bool = True) -> dict:
    output_path = params.get("OUTPUT")
    if overwrite and output_path:
        delete_existing_file(output_path)

    cmd = [get_qgis_process_cmd(), "run", algorithm_id, "--"]
    for key, value in params.items():
        if isinstance(value, bool):
            value = str(value).lower()
        cmd.append(f"{key}={value}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )

    return {
        "algorithm": algorithm_id,
        "params": params,
        "command": " ".join(cmd),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "success": result.returncode == 0,
    }
