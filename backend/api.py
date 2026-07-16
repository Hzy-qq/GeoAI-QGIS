from __future__ import annotations

import os
import shutil
import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from geoai_agent.config import PROJECT_ROOT, env_bool, env_float, env_str
from geoai_agent.qgis_runner import get_qgis_process_cmd
from geoai_agent.redis_bus import check_redis
from geoai_agent.progress import read_progress

from .database import Database
from .schemas import (
    ConversationCreate,
    ConversationListResponse,
    ConversationMessagesResponse,
    ConversationResponse,
    ErrorResponse,
    HealthResponse,
    TaskCreate,
    TaskListResponse,
    TaskResponse,
    TaskResultResponse,
    TaskTraceResponse,
)
from .service import (
    ConversationNotFoundError,
    TaskNotFoundError,
    TaskNotReadyError,
    TaskService,
)
from .worker_health import read_worker_health


def _service(request: Request) -> TaskService:
    return TaskService(request.app.state.database)


def _with_worker_runtime(task: dict) -> dict:
    worker = read_worker_health()
    return {
        **task,
        "worker_state": worker.get("state"),
        "active_worker_task_id": worker.get("current_task_id"),
    }


def _path_exists_or_command(command: str) -> bool:
    path = Path(command)
    return path.exists() if path.is_absolute() else shutil.which(command) is not None


def _check_chroma() -> tuple[bool, str]:
    if not env_bool("READINESS_CHECK_CHROMA", True):
        return True, "disabled"
    try:
        import chromadb

        path = Path(env_str("CHROMA_PATH", "outputs/chroma"))
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.exists():
            return False, f"missing path: {path}"
        client = chromadb.PersistentClient(path=str(path))
        collection = env_str("CHROMA_COLLECTION", "geoai_state5_knowledge")
        names = {item.name for item in client.list_collections()}
        return (collection in names, collection if collection in names else f"missing: {collection}")
    except Exception as exc:
        return False, str(exc)


def create_app(database_url: str | None = None) -> FastAPI:
    database = Database(database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if env_bool("DB_AUTO_CREATE", True):
            database.create_schema()
        app.state.database = database
        yield
        database.dispose()

    app = FastAPI(
        title="GeoAI-QGIS API",
        version="1.0.0-final",
        description=(
            "Visual multi-turn GIS agent with SSE progress, map layers and "
            "multi-criteria site selection."
        ),
        lifespan=lifespan,
    )

    origins = [item.strip() for item in env_str("CORS_ORIGINS", "").split(",") if item.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "Idempotency-Key"],
        )

    @app.exception_handler(TaskNotFoundError)
    async def not_found_handler(_request: Request, exc: TaskNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"code": "TASK_NOT_FOUND", "message": str(exc)},
        )

    @app.exception_handler(ConversationNotFoundError)
    async def conversation_not_found_handler(
        _request: Request, exc: ConversationNotFoundError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"code": "CONVERSATION_NOT_FOUND", "message": str(exc)},
        )

    @app.exception_handler(TaskNotReadyError)
    async def not_ready_handler(_request: Request, exc: TaskNotReadyError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "TASK_NOT_READY", "message": f"Current task status: {exc}"},
        )

    @app.post(
        "/api/v1/tasks",
        response_model=TaskResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses={422: {"model": ErrorResponse}},
    )
    def create_task(
        body: TaskCreate,
        request: Request,
        idempotency_key: str | None = Header(
            default=None, alias="Idempotency-Key", min_length=8, max_length=100,
        ),
    ) -> dict:
        return _with_worker_runtime(
            _service(request).create_task(
                body.query,
                body.user_id,
                idempotency_key,
                body.conversation_id,
            )
        )

    @app.post(
        "/api/v1/conversations",
        response_model=ConversationResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_conversation(body: ConversationCreate, request: Request) -> dict:
        return _service(request).create_conversation(body.user_id, body.title)

    @app.get(
        "/api/v1/conversations",
        response_model=ConversationListResponse,
    )
    def list_user_conversations(
        request: Request,
        user_id: str = Query(default="anonymous", min_length=1, max_length=80),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict:
        return {"items": _service(request).list_conversations(user_id, limit)}

    @app.get(
        "/api/v1/conversations/{conversation_id}",
        response_model=ConversationResponse,
    )
    def get_conversation(conversation_id: str, request: Request) -> dict:
        return _service(request).get_conversation(conversation_id)

    @app.get(
        "/api/v1/conversations/{conversation_id}/messages",
        response_model=ConversationMessagesResponse,
    )
    def get_conversation_messages(conversation_id: str, request: Request) -> dict:
        return {
            "conversation_id": conversation_id,
            "items": _service(request).get_conversation_messages(conversation_id),
        }

    @app.get("/api/v1/tasks", response_model=TaskListResponse)
    def get_tasks(
        request: Request,
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        return {
            "items": [
                _with_worker_runtime(item)
                for item in _service(request).list_tasks(limit, offset)
            ],
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/v1/tasks/{task_id}", response_model=TaskResponse)
    def get_task(task_id: str, request: Request) -> dict:
        return _with_worker_runtime(_service(request).get_task(task_id))

    @app.get("/api/v1/tasks/{task_id}/result", response_model=TaskResultResponse)
    def get_result(task_id: str, request: Request) -> dict:
        return _service(request).get_result(task_id)

    @app.get("/api/v1/tasks/{task_id}/trace", response_model=TaskTraceResponse)
    def get_trace(task_id: str, request: Request) -> dict:
        return _service(request).get_public_trace(task_id)

    @app.get("/api/v1/tasks/{task_id}/artifacts/{artifact_id}/download")
    def download_artifact(task_id: str, artifact_id: int, request: Request) -> FileResponse:
        artifact = _service(request).get_artifact(task_id, artifact_id)
        path = Path(artifact.path).resolve()
        output_root = (PROJECT_ROOT / "outputs" / "tasks").resolve()
        if output_root not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail="Artifact file is unavailable.")
        return FileResponse(path, media_type=artifact.media_type, filename=path.name)

    @app.get("/api/v1/tasks/{task_id}/artifacts/{artifact_id}/geojson")
    def artifact_geojson(
        task_id: str,
        artifact_id: int,
        request: Request,
        limit: int = Query(default=5000, ge=1, le=20000),
    ) -> JSONResponse:
        artifact = _service(request).get_artifact(task_id, artifact_id)
        path = Path(artifact.path).resolve()
        output_root = (PROJECT_ROOT / "outputs" / "tasks").resolve()
        if artifact.kind != "result" or output_root not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail="Result layer is unavailable.")
        try:
            import geopandas as gpd

            layer = gpd.read_file(path).head(limit)
            if layer.crs is not None and str(layer.crs) != "EPSG:4326":
                layer = layer.to_crs("EPSG:4326")
            payload = json.loads(layer.to_json())
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail=f"Could not convert result layer to GeoJSON: {exc}"
            ) from exc
        return JSONResponse(payload)

    @app.get("/api/v1/tasks/{task_id}/events")
    async def task_events(
        task_id: str,
        request: Request,
        poll_seconds: float = Query(default=0.75, ge=0.25, le=5),
    ) -> StreamingResponse:
        service = _service(request)
        service.get_task(task_id)

        async def generate():
            cursor = 0
            last_status = ""
            pending_started = time.monotonic()
            pending_grace = max(
                0.5,
                env_float("TASK_PENDING_WORKER_GRACE_SECONDS", 20.0),
            )
            while True:
                if await request.is_disconnected():
                    return
                task = _with_worker_runtime(service.get_task(task_id))
                status_value = task["status"]
                if (
                    status_value == "PENDING"
                    and time.monotonic() - pending_started >= pending_grace
                ):
                    worker = read_worker_health()
                    if not worker["active"]:
                        detail = (
                            "No active GIS Worker is available. Restart with "
                            "`python scripts\\run_api.py`; it now starts a companion "
                            f"Worker automatically. {worker.get('detail', '')}"
                        )
                        service.fail_pending_without_worker(task_id, detail)
                        task = _with_worker_runtime(service.get_task(task_id))
                        status_value = task["status"]
                if status_value != last_status:
                    yield _sse("status", task)
                    last_status = status_value
                events, cursor = read_progress(task_id, cursor)
                for event in events:
                    yield _sse("progress", event)
                if status_value in {"SUCCEEDED", "FAILED"}:
                    if status_value == "SUCCEEDED":
                        result = service.get_result(task_id)
                        yield _sse("result", result)
                    else:
                        yield _sse(
                            "error",
                            {
                                "code": task.get("error_code"),
                                "message": task.get("error_message"),
                            },
                        )
                    yield _sse("complete", {"status": status_value})
                    return
                yield ": keep-alive\n\n"
                await asyncio.sleep(poll_seconds)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/health/live", response_model=HealthResponse)
    def live() -> dict:
        return {"status": "ok", "checks": {"process": True}}

    @app.get("/health/ready", response_model=HealthResponse)
    def ready(request: Request) -> JSONResponse:
        checks: dict[str, object] = {}
        try:
            request.app.state.database.check()
            checks["database"] = {"ready": True}
        except Exception as exc:
            checks["database"] = {"ready": False, "detail": str(exc)}

        chroma_ready, chroma_detail = _check_chroma()
        checks["chroma"] = {"ready": chroma_ready, "detail": chroma_detail}
        qgis_ready = _path_exists_or_command(get_qgis_process_cmd())
        checks["qgis"] = {"ready": qgis_ready, "detail": get_qgis_process_cmd()}
        worker = read_worker_health()
        checks["worker"] = {
            "ready": bool(worker["ready"]),
            "state": worker.get("state"),
            "age_seconds": worker.get("age_seconds"),
            "detail": worker.get("detail"),
        }
        redis = check_redis()
        checks["redis"] = {
            "ready": bool(redis["available"]) or not bool(redis["required"]),
            **redis,
        }
        all_ready = all(bool(value.get("ready")) for value in checks.values())
        return JSONResponse(
            status_code=200 if all_ready else 503,
            content={"status": "ready" if all_ready else "not_ready", "checks": checks},
        )

    @app.get("/", include_in_schema=False)
    def visual_frontend() -> FileResponse:
        frontend = PROJECT_ROOT / "frontend" / "index.html"
        if not frontend.exists():
            raise HTTPException(status_code=404, detail="Frontend is not installed.")
        return FileResponse(frontend, media_type="text/html")

    return app


def _sse(event: str, payload: object) -> str:
    data = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {data}\n\n"


app = create_app(os.getenv("DATABASE_URL"))
