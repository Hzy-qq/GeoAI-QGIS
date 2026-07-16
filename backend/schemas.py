from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


TaskStatus = Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED"]


class TaskCreate(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    user_id: str = Field(default="anonymous", min_length=1, max_length=80, pattern=r"^[\w.-]+$")
    conversation_id: str | None = Field(default=None, min_length=8, max_length=64)


class TaskResponse(BaseModel):
    task_id: str
    conversation_id: str
    status: TaskStatus
    query: str
    user_id: str
    idempotency_key: str
    idempotency_reused: bool = False
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    queue_position: int | None = None
    worker_state: str | None = None
    active_worker_task_id: str | None = None


class TaskListResponse(BaseModel):
    items: list[TaskResponse]
    limit: int
    offset: int


class ArtifactResponse(BaseModel):
    artifact_id: int
    kind: str
    media_type: str
    size_bytes: int | None
    download_url: str


class TaskResultResponse(BaseModel):
    task_id: str
    status: TaskStatus
    answer: str
    summary: dict[str, Any]
    evaluation: dict[str, Any]
    artifacts: list[ArtifactResponse]


class TaskTraceResponse(BaseModel):
    task_id: str
    trace: dict[str, Any]


class ErrorResponse(BaseModel):
    code: str
    message: str


class HealthResponse(BaseModel):
    status: str
    checks: dict[str, Any] = Field(default_factory=dict)


class ConversationCreate(BaseModel):
    user_id: str = Field(default="anonymous", min_length=1, max_length=80, pattern=r"^[\w.-]+$")
    title: str = Field(default="新会话", min_length=1, max_length=200)


class ConversationResponse(BaseModel):
    conversation_id: str
    user_id: str
    title: str
    state: dict[str, Any]
    summary: str
    turn_count: int
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    items: list[ConversationResponse]


class ConversationMessageResponse(BaseModel):
    message_id: str
    conversation_id: str
    task_id: str | None
    role: Literal["user", "assistant", "system"]
    content: str
    metadata: dict[str, Any]
    created_at: datetime


class ConversationMessagesResponse(BaseModel):
    conversation_id: str
    items: list[ConversationMessageResponse]
