from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from geoai_agent.config import env_str

from .models import (
    AgentTask,
    Artifact,
    Conversation,
    ConversationMessage,
    ConversationTurn,
    EvalRun,
    LlmCall,
    RetrievalHit,
    WorkflowStep,
    utc_now,
)


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def get_task(session: Session, task_id: str) -> AgentTask | None:
    return session.get(AgentTask, task_id)


def get_by_idempotency(
    session: Session,
    user_id: str,
    idempotency_key: str,
) -> AgentTask | None:
    statement = select(AgentTask).where(
        AgentTask.user_id == user_id,
        AgentTask.idempotency_key == idempotency_key,
    )
    return session.execute(statement).scalar_one_or_none()


def list_tasks(session: Session, limit: int, offset: int) -> list[AgentTask]:
    statement = (
        select(AgentTask)
        .order_by(AgentTask.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.execute(statement).scalars())


def pending_queue_position(session: Session, task: AgentTask) -> int | None:
    if task.status != "PENDING":
        return None
    statement = select(func.count()).select_from(AgentTask).where(
        AgentTask.status == "PENDING",
        or_(
            AgentTask.created_at < task.created_at,
            and_(AgentTask.created_at == task.created_at, AgentTask.id <= task.id),
        ),
    )
    return int(session.execute(statement).scalar_one())


def get_conversation(session: Session, conversation_id: str) -> Conversation | None:
    return session.get(Conversation, conversation_id)


def list_conversations(
    session: Session,
    user_id: str,
    limit: int = 20,
) -> list[Conversation]:
    statement = (
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(limit)
    )
    return list(session.execute(statement).scalars())


def get_conversation_turn(session: Session, task_id: str) -> ConversationTurn | None:
    return session.get(ConversationTurn, task_id)


def get_task_conversation(session: Session, task_id: str) -> Conversation | None:
    turn = get_conversation_turn(session, task_id)
    return None if turn is None else get_conversation(session, turn.conversation_id)


def list_conversation_messages(
    session: Session,
    conversation_id: str,
    limit: int | None = None,
) -> list[ConversationMessage]:
    statement = (
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
    )
    if limit is not None:
        statement = statement.limit(limit)
    return list(reversed(list(session.execute(statement).scalars())))


def add_conversation_message(
    session: Session,
    *,
    conversation_id: str,
    role: str,
    content: str,
    task_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ConversationMessage:
    import uuid

    message = ConversationMessage(
        id=uuid.uuid4().hex,
        conversation_id=conversation_id,
        task_id=task_id,
        role=role,
        content=content,
        metadata_json=json_text(metadata or {}),
    )
    session.add(message)
    session.flush()
    return message


def claim_next_task(session: Session, worker_id: str) -> AgentTask | None:
    statement = (
        select(AgentTask)
        .where(AgentTask.status == "PENDING")
        .order_by(AgentTask.created_at)
        .limit(1)
    )
    if session.bind is not None and session.bind.dialect.name == "mysql":
        statement = statement.with_for_update(skip_locked=True)
    task = session.execute(statement).scalar_one_or_none()
    if task is None:
        return None
    task.status = "RUNNING"
    task.worker_id = worker_id
    task.started_at = utc_now()
    task.updated_at = utc_now()
    session.commit()
    session.refresh(task)
    return task


def recover_stale_tasks(session: Session, stale_after_seconds: int) -> int:
    cutoff = utc_now() - timedelta(seconds=stale_after_seconds)
    statement = select(AgentTask).where(
        AgentTask.status == "RUNNING",
        AgentTask.started_at.is_not(None),
        AgentTask.started_at < cutoff,
    )
    tasks = list(session.execute(statement).scalars())
    for task in tasks:
        task.status = "FAILED"
        task.error_code = "WORKER_INTERRUPTED"
        task.error_message = "Worker stopped before the task completed. Submit a new task to retry."
        task.finished_at = utc_now()
        task.updated_at = utc_now()
    session.commit()
    return len(tasks)


def expire_stale_pending_tasks(session: Session, max_age_seconds: int) -> int:
    if max_age_seconds <= 0:
        return 0
    cutoff = utc_now() - timedelta(seconds=max_age_seconds)
    statement = select(AgentTask).where(
        AgentTask.status == "PENDING",
        AgentTask.created_at < cutoff,
    )
    tasks = list(session.execute(statement).scalars())
    for task in tasks:
        task.status = "FAILED"
        task.error_code = "QUEUE_EXPIRED"
        task.error_message = (
            "The queued task expired before a Worker claimed it. Submit it again if still needed."
        )
        task.finished_at = utc_now()
        task.updated_at = utc_now()
    session.commit()
    return len(tasks)


def mark_task_failed(
    session: Session,
    task_id: str,
    error_code: str,
    error_message: str,
) -> None:
    task = get_task(session, task_id)
    if task is None:
        return
    task.status = "FAILED"
    task.error_code = error_code[:80]
    task.error_message = error_message
    task.finished_at = utc_now()
    task.updated_at = utc_now()
    session.commit()


def mark_pending_task_failed(
    session: Session,
    task_id: str,
    error_code: str,
    error_message: str,
) -> bool:
    task = get_task(session, task_id)
    if task is None or task.status != "PENDING":
        return False
    task.status = "FAILED"
    task.error_code = error_code[:80]
    task.error_message = error_message
    task.finished_at = utc_now()
    task.updated_at = utc_now()
    session.commit()
    return True


def mark_running_task_failed(
    session: Session,
    task_id: str,
    worker_id: str,
    error_message: str,
) -> bool:
    task = get_task(session, task_id)
    if task is None or task.status != "RUNNING" or task.worker_id != worker_id:
        return False
    task.status = "FAILED"
    task.error_code = "WORKER_INTERRUPTED"
    task.error_message = error_message
    task.finished_at = utc_now()
    task.updated_at = utc_now()
    session.commit()
    return True


def _score(document: dict[str, Any]) -> float | None:
    for key in ("rerank_score", "score", "similarity"):
        value = document.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    distance = document.get("distance")
    return float(1.0 - distance) if isinstance(distance, (int, float)) else None


def replace_task_details(
    session: Session,
    task: AgentTask,
    trace: dict[str, Any],
    trace_path: Path,
) -> None:
    for model in (WorkflowStep, RetrievalHit, LlmCall, Artifact, EvalRun):
        session.execute(delete(model).where(model.task_id == task.id))

    execution = trace.get("execution_trace") or {}
    for index, step in enumerate(execution.get("steps", []), start=1):
        session.add(WorkflowStep(
            task_id=task.id,
            step_no=int(step.get("step", index)),
            tool=str(step.get("tool", "unknown")),
            params_json=json_text(step.get("params", {})),
            status="SUCCEEDED" if step.get("success") else "FAILED",
            duration_ms=step.get("duration_ms"),
            error_type=step.get("error_type"),
            error_message=step.get("error_message") or None,
        ))

    for rank, document in enumerate(trace.get("retrieved_docs", []), start=1):
        session.add(RetrievalHit(
            task_id=task.id,
            document_id=str(document.get("id", f"rank-{rank}")),
            rank=rank,
            score=_score(document),
            metadata_json=json_text(document.get("metadata", {})),
        ))

    provider = env_str("LLM_PROVIDER", "deepseek")
    model = env_str("LLM_MODEL", "deepseek-v4-flash")
    for event in trace.get("node_trace", []):
        if event.get("node") not in {"plan", "summarize"}:
            continue
        session.add(LlmCall(
            task_id=task.id,
            purpose=str(event["node"]),
            provider=provider,
            model=model,
            status=str(event.get("status", "unknown")).upper(),
            duration_ms=event.get("duration_ms"),
            error_message=event.get("error"),
        ))

    evaluation = trace.get("evaluation_result") or {}
    if evaluation:
        session.add(EvalRun(
            task_id=task.id,
            evaluator_version="state5-deterministic-v1",
            passed=bool(evaluation.get("passed")),
            metrics_json=json_text({
                key: value for key, value in evaluation.items()
                if key not in {"passed", "issues"}
            }),
            issues_json=json_text(evaluation.get("issues", [])),
        ))

    result_file = evaluation.get("result_file")
    if result_file:
        path = Path(result_file)
        session.add(Artifact(
            task_id=task.id,
            kind="result",
            path=str(path),
            media_type="application/geopackage+sqlite3",
            size_bytes=path.stat().st_size if path.exists() else None,
        ))
    session.add(Artifact(
        task_id=task.id,
        kind="trace",
        path=str(trace_path),
        media_type="application/json",
        size_bytes=trace_path.stat().st_size if trace_path.exists() else None,
    ))

    summary = trace.get("summary") or {}
    task.status = "SUCCEEDED" if trace.get("success") else "FAILED"
    task.trace_id = task.id
    task.workspace = trace.get("workspace")
    task.answer = summary.get("answer")
    task.result_payload = json_text({
        "summary": summary,
        "evaluation": evaluation,
        "plan": {
            key: (trace.get("plan") or {}).get(key)
            for key in ("task_type", "region_name", "data_requirements", "planner_mode")
        },
    })
    if not trace.get("success"):
        unsupported = (trace.get("plan") or {}).get("supported") is False
        task.error_code = "UNSUPPORTED_TASK" if unsupported else "AGENT_EXECUTION_FAILED"
        task.error_message = summary.get("answer") or "Agent execution failed."
    else:
        task.error_code = None
        task.error_message = None
    task.finished_at = utc_now()
    task.updated_at = utc_now()
    session.commit()


def list_artifacts(session: Session, task_id: str) -> list[Artifact]:
    statement = select(Artifact).where(Artifact.task_id == task_id).order_by(Artifact.id)
    return list(session.execute(statement).scalars())


def get_artifact(session: Session, task_id: str, artifact_id: int) -> Artifact | None:
    statement = select(Artifact).where(
        Artifact.id == artifact_id,
        Artifact.task_id == task_id,
    )
    return session.execute(statement).scalar_one_or_none()
