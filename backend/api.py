from __future__ import annotations

import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from geoai_agent.config import PROJECT_ROOT, env_bool, env_str
from geoai_agent.qgis_runner import get_qgis_process_cmd

from .database import Database
from .schemas import (
    ConversationCreate,
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


def _service(request: Request) -> TaskService:
    return TaskService(request.app.state.database)


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
        version="0.7.0-state5",
        description="Multi-turn GIS agent service backed by LangGraph, QGIS and SQL memory.",
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
        return _service(request).create_task(
            body.query, body.user_id, idempotency_key, body.conversation_id,
        )

    @app.post(
        "/api/v1/conversations",
        response_model=ConversationResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_conversation(body: ConversationCreate, request: Request) -> dict:
        return _service(request).create_conversation(body.user_id, body.title)

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
            "items": _service(request).list_tasks(limit, offset),
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/v1/tasks/{task_id}", response_model=TaskResponse)
    def get_task(task_id: str, request: Request) -> dict:
        return _service(request).get_task(task_id)

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
        all_ready = all(bool(value.get("ready")) for value in checks.values())
        return JSONResponse(
            status_code=200 if all_ready else 503,
            content={"status": "ready" if all_ready else "not_ready", "checks": checks},
        )

    return app


app = create_app(os.getenv("DATABASE_URL"))
