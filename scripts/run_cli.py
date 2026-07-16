from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.database import Database
from backend.service import TaskService


def execute_turn(service: TaskService, conversation_id: str, query: str, user_id: str) -> None:
    task = service.create_task(
        query,
        user_id,
        f"cli-{uuid.uuid4().hex}",
        conversation_id,
    )
    trace = service.execute_claimed(task["task_id"], query)
    print("\n最终回答：")
    print((trace.get("summary") or {}).get("answer", "没有生成结果。"))
    print("Task ID:", task["task_id"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the GeoAI-QGIS Final multi-turn CLI.")
    parser.add_argument("query", nargs="*")
    parser.add_argument("--conversation-id")
    parser.add_argument("--user-id", default="cli-user")
    args = parser.parse_args()

    database = Database()
    database.create_schema()
    service = TaskService(database)
    conversation_id = args.conversation_id
    if conversation_id:
        service.get_conversation(conversation_id)
    else:
        conversation_id = service.create_conversation(args.user_id, "CLI 多轮会话")[
            "conversation_id"
        ]
    print("Conversation ID:", conversation_id)

    initial = " ".join(args.query).strip()
    if initial:
        execute_turn(service, conversation_id, initial, args.user_id)
        return
    print("输入 exit 结束。后续启动可使用 --conversation-id 恢复同一会话。")
    while True:
        query = input("\n请输入空间分析任务：").strip()
        if query.lower() in {"exit", "quit", "q"}:
            break
        if query:
            execute_turn(service, conversation_id, query, args.user_id)


if __name__ == "__main__":
    main()
