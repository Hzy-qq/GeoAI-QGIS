from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid

from geoai_agent.config import env_float, env_int

from .database import Database
from .service import TaskService
from .worker_health import write_worker_heartbeat


LOGGER = logging.getLogger("geoai.worker")


class TaskWorker:
    def __init__(self, database: Database | None = None, worker_id: str | None = None) -> None:
        self.database = database or Database()
        self.service = TaskService(self.database)
        self.worker_id = worker_id or (
            f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        )
        self._state_lock = threading.Lock()
        self._state = "starting"
        self._current_task_id: str | None = None
        self._detail: str | None = None
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    def _heartbeat(self) -> None:
        with self._state_lock:
            state = self._state
            task_id = self._current_task_id
            detail = self._detail
        try:
            write_worker_heartbeat(
                self.worker_id,
                state,
                current_task_id=task_id,
                detail=detail,
            )
        except OSError:
            LOGGER.exception("Could not write Worker heartbeat")

    def _set_state(
        self,
        state: str,
        *,
        task_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        with self._state_lock:
            self._state = state
            self._current_task_id = task_id
            self._detail = detail
        self._heartbeat()

    def _start_heartbeat(self) -> None:
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return
        interval = max(0.5, env_float("WORKER_HEARTBEAT_SECONDS", 2.0))
        self._heartbeat_stop.clear()

        def beat_forever() -> None:
            while not self._heartbeat_stop.wait(interval):
                self._heartbeat()

        self._heartbeat_thread = threading.Thread(
            target=beat_forever,
            name="geoai-worker-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()
        self._heartbeat()

    def close(self) -> None:
        self._heartbeat_stop.set()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=2.0)
        self._set_state("stopped")
        self.database.dispose()

    def prepare(self) -> int:
        self._start_heartbeat()
        self.database.create_schema()
        expired = self.service.expire_pending(
            env_int("WORKER_PENDING_MAX_AGE_SECONDS", 900)
        )
        if expired:
            LOGGER.warning("Expired %s stale queued task(s)", expired)
        stale_seconds = env_int("WORKER_STALE_AFTER_SECONDS", 1200)
        recovered = self.service.recover_stale(stale_seconds)
        self._set_state("idle")
        return recovered

    def run_once(self) -> bool:
        try:
            claimed = self.service.claim_next(self.worker_id)
        except Exception as exc:
            self._set_state("degraded", detail=f"Task polling failed: {exc}")
            LOGGER.exception("Worker polling failed; it will retry")
            return False
        if claimed is None:
            self._set_state("idle")
            return False
        task_id = claimed["task_id"]
        self._set_state("running", task_id=task_id)
        LOGGER.info("Claimed task %s", task_id)
        try:
            trace = self.service.execute_claimed(task_id, claimed["query"])
            LOGGER.info("Finished task %s success=%s", task_id, trace.get("success"))
        except Exception as exc:
            LOGGER.exception("Task %s failed", task_id)
            self._set_state("degraded", detail=f"Task {task_id} failed: {exc}")
        except BaseException as exc:
            self.service.fail_interrupted_running_task(
                task_id,
                self.worker_id,
                f"Worker interrupted while executing the task: {type(exc).__name__}",
            )
            raise
        finally:
            self._set_state("idle")
        return True

    def run_forever(self, poll_seconds: float = 2.0) -> None:
        try:
            recovered = self.prepare()
            if recovered:
                LOGGER.warning("Marked %s interrupted task(s) as failed", recovered)
            LOGGER.info("Worker %s started", self.worker_id)
            while True:
                if not self.run_once():
                    time.sleep(poll_seconds)
        finally:
            self.close()
