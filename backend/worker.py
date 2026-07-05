from __future__ import annotations

import logging
import os
import socket
import time
import uuid

from geoai_agent.config import env_int

from .database import Database
from .service import TaskService


LOGGER = logging.getLogger("geoai.worker")


class TaskWorker:
    def __init__(self, database: Database | None = None, worker_id: str | None = None) -> None:
        self.database = database or Database()
        self.service = TaskService(self.database)
        self.worker_id = worker_id or (
            f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        )

    def prepare(self) -> int:
        self.database.create_schema()
        stale_seconds = env_int("WORKER_STALE_AFTER_SECONDS", 1200)
        return self.service.recover_stale(stale_seconds)

    def run_once(self) -> bool:
        claimed = self.service.claim_next(self.worker_id)
        if claimed is None:
            return False
        task_id = claimed["task_id"]
        LOGGER.info("Claimed task %s", task_id)
        try:
            trace = self.service.execute_claimed(task_id, claimed["query"])
            LOGGER.info("Finished task %s success=%s", task_id, trace.get("success"))
        except Exception:
            LOGGER.exception("Task %s failed", task_id)
        return True

    def run_forever(self, poll_seconds: float = 2.0) -> None:
        recovered = self.prepare()
        if recovered:
            LOGGER.warning("Marked %s interrupted task(s) as failed", recovered)
        LOGGER.info("Worker %s started", self.worker_id)
        while True:
            if not self.run_once():
                time.sleep(poll_seconds)

