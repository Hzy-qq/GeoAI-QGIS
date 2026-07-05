# GeoAI-QGIS State 5

State 5 is the final multi-turn version of this project. It keeps the State 4 asynchronous
FastAPI + Worker + MySQL backend and adds conversation memory, reference resolution and a
durable LangGraph checkpointer.

## What changed

- Every task belongs to a `conversation_id` (also used as LangGraph `thread_id`).
- MySQL stores complete conversations, messages and structured memory.
- SQLite Checkpointer stores LangGraph thread checkpoints under `outputs/checkpoints/`.
- The context resolver can inherit a region from earlier turns or ask for clarification.
- Recent messages, a bounded old-message summary and structured slots are stored separately.
- An adjacency workflow was added for the three-turn Nanjing acceptance scenario.

RAG and memory are different subsystems. Chroma retrieves GIS knowledge for planning; MySQL
and the Checkpointer preserve user-specific conversation context.

## Supported tasks

1. Administrative area: `帮我计算南京市的面积`
2. Adjacent regions: `它周围有哪些城市？`
3. University count: `再统计这里面的高校数量`
4. Road length around all OSM universities: `统计南京市所有大学附近500米道路总长度`

The adjacency task uses `data/fixtures/nanjing_neighbor_cities.gpkg`, a bundled GADM 4.1
academic test fixture. It is deterministic for project evaluation but may differ from current
official administrative divisions. The other tasks use allowlisted OpenStreetMap services.

## Architecture

```text
Client / Swagger / CLI
  -> FastAPI creates task + conversation message in MySQL
  -> Worker claims task
  -> outer Conversation LangGraph
       -> context_resolver
       -> clarify OR inner GIS LangGraph
  -> inner GIS LangGraph
       -> Chroma retrieval + reranking
       -> constrained planner + schema validation
       -> tool execution + deterministic evaluation
       -> LLM result summary
  -> MySQL persists result, messages and structured slots
  -> SQLiteSaver persists the LangGraph thread checkpoint
```

## Installation

Clone the repository and use a Python environment that can access QGIS:

```powershell
git clone https://github.com/Hzy-qq/GeoAI-QGIS.git
cd GeoAI-QGIS
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Set `LLM_API_KEY`, `QGIS_PROCESS_CMD`, the two MySQL passwords and `DATABASE_URL` in `.env`.
Do not commit `.env`.

Start MySQL with Docker Compose. Stop any existing service already using port 3306 first:

```powershell
docker compose up -d mysql
docker compose ps
```

Build the State 5 Chroma collection once:

```powershell
python scripts\build_knowledge.py
```

## Run

Terminal 1:

```powershell
python scripts\run_api.py
```

Terminal 2:

```powershell
python scripts\run_worker.py
```

Open `http://127.0.0.1:8000/docs`.

1. Call `POST /api/v1/conversations` and keep the returned `conversation_id`.
2. Call `POST /api/v1/tasks` with the first query and that `conversation_id`.
3. Poll `GET /api/v1/tasks/{task_id}` and read `GET /api/v1/tasks/{task_id}/result`.
4. Submit later queries with the same `conversation_id`.
5. Inspect memory using `GET /api/v1/conversations/{conversation_id}` and `/messages`.

Example task body:

```json
{
  "query": "它周围有哪些城市？",
  "user_id": "demo-user",
  "conversation_id": "the-id-returned-by-the-conversation-api"
}
```

For a local interactive session:

```powershell
python scripts\run_cli.py
```

The CLI prints its conversation ID. Restore it later with
`scripts\run_cli.py --conversation-id <id>`.

## Tests

```powershell
python scripts\evaluate.py
python scripts\evaluate.py --check-runtime
```

The offline suite covers API idempotency, conversation isolation and clarification, schema
guardrails, workspace path safety, execution retry bounds, result evaluation and the bundled
Nanjing adjacency topology workflow. Live LLM/network evaluation remains opt-in.

## Repository safety

`.env`, MySQL data, Chroma indexes, checkpoints, downloaded OSM data and task outputs are not
committed. Only `.env.example`, source code, tests and the small licensed evaluation fixture are
kept in the project.
