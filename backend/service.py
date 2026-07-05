from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.exc import IntegrityError

from geoai_agent.config import env_int
from geoai_agent.executor import save_trace
from geoai_agent.task_workspace import TaskWorkspace

from .database import Database
from .models import AgentTask, Artifact, Conversation, ConversationTurn, utc_now
from .repository import (
    add_conversation_message,
    claim_next_task,
    get_artifact,
    get_by_idempotency,
    get_conversation,
    get_conversation_turn,
    get_task,
    get_task_conversation,
    list_artifacts,
    list_conversation_messages,
    list_tasks,
    mark_task_failed,
    recover_stale_tasks,
    replace_task_details,
)


class TaskNotFoundError(LookupError):
    pass


class TaskNotReadyError(RuntimeError):
    pass


class ConversationNotFoundError(LookupError):
    pass


def _loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default


def task_dict(task: AgentTask, conversation_id: str, reused: bool = False) -> dict[str, Any]:
    return {
        "task_id": task.id,
        "conversation_id": conversation_id,
        "status": task.status,
        "query": task.query,
        "user_id": task.user_id,
        "idempotency_key": task.idempotency_key,
        "idempotency_reused": reused,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "error_code": task.error_code,
        "error_message": task.error_message,
    }


def conversation_dict(conversation: Conversation) -> dict[str, Any]:
    return {
        "conversation_id": conversation.id,
        "user_id": conversation.user_id,
        "title": conversation.title,
        "state": _loads(conversation.state_json, {}),
        "summary": conversation.summary,
        "turn_count": conversation.turn_count,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
    }


def message_dict(message) -> dict[str, Any]:
    return {
        "message_id": message.id,
        "conversation_id": message.conversation_id,
        "task_id": message.task_id,
        "role": message.role,
        "content": message.content,
        "metadata": _loads(message.metadata_json, {}),
        "created_at": message.created_at,
    }


def artifact_dict(task_id: str, artifact: Artifact) -> dict[str, Any]:
    return {
        "artifact_id": artifact.id,
        "kind": artifact.kind,
        "media_type": artifact.media_type,
        "size_bytes": artifact.size_bytes,
        "download_url": f"/api/v1/tasks/{task_id}/artifacts/{artifact.id}/download",
    }


def _compact_summary(messages: list, recent_limit: int, max_chars: int) -> str:
    older = messages[:-recent_limit] if recent_limit > 0 else messages
    lines = []
    for message in older:
        content = " ".join(message.content.split())
        lines.append(f"{message.role}: {content[:240]}")
    summary = "\n".join(lines)
    return summary[-max_chars:]


class TaskService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_conversation(self, user_id: str, title: str) -> dict[str, Any]:
        with self.database.session() as session:
            conversation = Conversation(
                id=uuid.uuid4().hex,
                user_id=user_id,
                title=title.strip(),
                state_json="{}",
                summary="",
                turn_count=0,
            )
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            return conversation_dict(conversation)

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            conversation = get_conversation(session, conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(conversation_id)
            return conversation_dict(conversation)

    def get_conversation_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        with self.database.session() as session:
            if get_conversation(session, conversation_id) is None:
                raise ConversationNotFoundError(conversation_id)
            return [
                message_dict(item)
                for item in list_conversation_messages(session, conversation_id)
            ]

    def create_task(
        self,
        query: str,
        user_id: str,
        idempotency_key: str | None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        key = idempotency_key or uuid.uuid4().hex
        with self.database.session() as session:
            existing = get_by_idempotency(session, user_id, key)
            if existing is not None:
                turn = get_conversation_turn(session, existing.id)
                if turn is None:
                    raise RuntimeError("Idempotent task is missing its conversation turn.")
                return task_dict(existing, turn.conversation_id, reused=True)

            conversation = get_conversation(session, conversation_id) if conversation_id else None
            if conversation_id and conversation is None:
                raise ConversationNotFoundError(conversation_id)
            if conversation is not None and conversation.user_id != user_id:
                raise ConversationNotFoundError(conversation_id or "")
            if conversation is None:
                conversation = Conversation(
                    id=uuid.uuid4().hex,
                    user_id=user_id,
                    title=query.strip()[:200],
                    state_json="{}",
                    summary="",
                    turn_count=0,
                )
                session.add(conversation)
                session.flush()

            task = AgentTask(
                id=uuid.uuid4().hex,
                user_id=user_id,
                idempotency_key=key,
                query=query.strip(),
                status="PENDING",
            )
            session.add(task)
            session.flush()
            user_message = add_conversation_message(
                session,
                conversation_id=conversation.id,
                task_id=task.id,
                role="user",
                content=task.query,
            )
            session.add(ConversationTurn(
                task_id=task.id,
                conversation_id=conversation.id,
                user_message_id=user_message.id,
            ))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = get_by_idempotency(session, user_id, key)
                if existing is None:
                    raise
                turn = get_conversation_turn(session, existing.id)
                if turn is None:
                    raise RuntimeError("Idempotent task is missing its conversation turn.")
                return task_dict(existing, turn.conversation_id, reused=True)
            session.refresh(task)
            return task_dict(task, conversation.id)

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            task = get_task(session, task_id)
            turn = get_conversation_turn(session, task_id)
            if task is None or turn is None:
                raise TaskNotFoundError(task_id)
            return task_dict(task, turn.conversation_id)

    def list_tasks(self, limit: int, offset: int) -> list[dict[str, Any]]:
        with self.database.session() as session:
            items = []
            for task in list_tasks(session, limit, offset):
                turn = get_conversation_turn(session, task.id)
                if turn is not None:
                    items.append(task_dict(task, turn.conversation_id))
            return items

    def get_result(self, task_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            task = get_task(session, task_id)
            if task is None:
                raise TaskNotFoundError(task_id)
            if task.status != "SUCCEEDED":
                raise TaskNotReadyError(task.status)
            payload = _loads(task.result_payload, {})
            return {
                "task_id": task.id,
                "status": task.status,
                "answer": task.answer or "",
                "summary": payload.get("summary", {}),
                "evaluation": payload.get("evaluation", {}),
                "artifacts": [
                    artifact_dict(task.id, artifact)
                    for artifact in list_artifacts(session, task.id)
                ],
            }

    def get_public_trace(self, task_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            task = get_task(session, task_id)
            if task is None:
                raise TaskNotFoundError(task_id)
            trace_artifact = next(
                (item for item in list_artifacts(session, task.id) if item.kind == "trace"),
                None,
            )
        if trace_artifact is None or not Path(trace_artifact.path).exists():
            raise TaskNotReadyError(task.status)
        trace = json.loads(Path(trace_artifact.path).read_text(encoding="utf-8"))
        return {"task_id": task_id, "trace": _public_trace(trace)}

    def get_artifact(self, task_id: str, artifact_id: int) -> Artifact:
        with self.database.session() as session:
            artifact = get_artifact(session, task_id, artifact_id)
            if artifact is None:
                raise TaskNotFoundError(f"{task_id}/{artifact_id}")
            session.expunge(artifact)
            return artifact

    def claim_next(self, worker_id: str) -> dict[str, str] | None:
        with self.database.session() as session:
            task = claim_next_task(session, worker_id)
            return None if task is None else {"task_id": task.id, "query": task.query}

    def recover_stale(self, stale_after_seconds: int) -> int:
        with self.database.session() as session:
            return recover_stale_tasks(session, stale_after_seconds)

    def execute_claimed(self, task_id: str, query: str) -> dict[str, Any]:
        try:
            from geoai_agent.conversation_agent import load_inner_trace, run_conversation_turn

            recent_limit = env_int("CONVERSATION_RECENT_MESSAGES", 6)
            with self.database.session() as session:
                conversation = get_task_conversation(session, task_id)
                if conversation is None:
                    raise ConversationNotFoundError(task_id)
                memory = _loads(conversation.state_json, {})
                recent = list_conversation_messages(session, conversation.id, recent_limit)
                context = {
                    "conversation_id": conversation.id,
                    "summary": conversation.summary,
                    "recent_messages": [
                        {"role": item.role, "content": item.content} for item in recent
                    ],
                }

            turn_result = run_conversation_turn(
                conversation_id=context["conversation_id"],
                task_id=task_id,
                user_query=query,
                memory=memory,
                conversation_summary=context["summary"],
                recent_messages=context["recent_messages"],
            )
            trace = load_inner_trace(turn_result)
            if trace is None:
                workspace = TaskWorkspace.create(task_id)
                trace = {
                    "agent": "GeoAI State 5 Conversation Agent",
                    "task_id": task_id,
                    "workspace": str(workspace.root),
                    "user_query": query,
                    "plan": {
                        "supported": True,
                        "task_type": "clarification",
                        "region_name": "",
                        "data_requirements": [],
                        "planner_mode": "context_resolver",
                    },
                    "workflow": None,
                    "execution_trace": None,
                    "evaluation_result": {"passed": True, "issues": []},
                    "summary": {
                        "answer": turn_result["answer"],
                        "answer_source": "clarification",
                    },
                    "node_trace": [{"node": "context_resolver", "status": "clarify"}],
                    "success": True,
                }
            trace["conversation"] = {
                "conversation_id": context["conversation_id"],
                "original_query": query,
                "resolved_query": turn_result.get("resolved_query", query),
                "resolution_source": turn_result.get("resolution_source", "none"),
                "action": turn_result.get("action", "execute"),
                "memory_before": memory,
                "memory_update": turn_result.get("memory_update", {}),
            }
            trace_path = Path(trace["workspace"]) / "trace" / "agent_trace.json"
            save_trace(trace, trace_path)

            with self.database.session() as session:
                task = get_task(session, task_id)
                conversation = get_conversation(session, context["conversation_id"])
                if task is None or conversation is None:
                    raise TaskNotFoundError(task_id)
                replace_task_details(session, task, trace, trace_path)
                memory_after = _loads(conversation.state_json, {})
                memory_after.update(turn_result.get("memory_update") or {})
                if turn_result.get("region_name"):
                    memory_after["current_region"] = turn_result["region_name"]
                add_conversation_message(
                    session,
                    conversation_id=conversation.id,
                    task_id=task_id,
                    role="assistant",
                    content=(trace.get("summary") or {}).get("answer", ""),
                    metadata={
                        "action": turn_result.get("action"),
                        "task_type": turn_result.get("task_type"),
                        "resolved_query": turn_result.get("resolved_query"),
                    },
                )
                conversation.state_json = json.dumps(memory_after, ensure_ascii=False)
                conversation.turn_count += 1
                conversation.updated_at = utc_now()
                all_messages = list_conversation_messages(session, conversation.id)
                conversation.summary = _compact_summary(
                    all_messages,
                    recent_limit,
                    env_int("CONVERSATION_SUMMARY_MAX_CHARS", 2000),
                )
                session.commit()
            return trace
        except Exception as exc:
            with self.database.session() as session:
                mark_task_failed(session, task_id, type(exc).__name__, str(exc))
            raise


def _public_trace(trace: dict[str, Any]) -> dict[str, Any]:
    plan = trace.get("plan") or {}
    execution = trace.get("execution_trace") or {}
    return {
        "task_id": trace.get("task_id"),
        "user_query": trace.get("user_query"),
        "success": trace.get("success"),
        "conversation": trace.get("conversation", {}),
        "plan": {
            key: plan.get(key)
            for key in (
                "supported", "reason", "task_type", "region_name",
                "data_requirements", "planner_mode",
            )
        },
        "retrieved_docs": [
            {
                "id": item.get("id"),
                "metadata": item.get("metadata", {}),
                "score": item.get("rerank_score", item.get("score")),
            }
            for item in trace.get("retrieved_docs", [])
        ],
        "resolved_datasets": trace.get("resolved_datasets", []),
        "workflow": trace.get("workflow"),
        "execution": {
            "success": execution.get("success"),
            "tool_calls_used": execution.get("tool_calls_used"),
            "steps": [
                {
                    key: step.get(key)
                    for key in (
                        "step", "tool", "success", "duration_ms", "error_type",
                        "error_message", "metrics",
                    )
                }
                for step in execution.get("steps", [])
            ],
        },
        "evaluation": trace.get("evaluation_result"),
        "summary": trace.get("summary"),
        "node_trace": trace.get("node_trace", []),
    }
