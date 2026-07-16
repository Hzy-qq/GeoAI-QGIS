# State 6: Multi-turn Visual GIS Agent

State 6 extends the verified State 5 backend without modifying the previous stage.

## Added capabilities

- `GET /` serves a Leaflet map frontend.
- The frontend displays full user/assistant message history, restores recent conversations,
  supports a new-conversation action and submits follow-up turns with the same conversation ID.
- `GET /api/v1/conversations?user_id=...` lists the user's recent conversations.
- `GET /api/v1/tasks/{task_id}/events` streams task status and LangGraph node events as SSE.
- `GET /api/v1/tasks/{task_id}/artifacts/{artifact_id}/geojson` converts result layers for map display.
- Existing artifact downloads continue to provide the original GeoPackage result.
- A deterministic `multi_criteria_site_selection` tool ranks grid cells using:
  - road accessibility (45%);
  - university/facility accessibility (35%);
  - distance from the administrative boundary (20%).
- Reusable spatial-analysis tools add generic OSM POI count/service-area/density analysis,
  nearest-main-road distance, main-road density and advanced campus site selection. Road
  workflows intentionally exclude local streets and use motorway/trunk/primary/secondary classes.
  A transient 504 or endpoint timeout now causes only the failed road bbox to be split into four
  smaller requests; successful batches are retained and the whole GIS workflow is not replayed.
  In the default `auto` mode, the primary road source is instead the official OSM Shortbread
  vector-tile service, cached under `outputs/data_cache/vector_tiles`; Overpass remains available
  as an explicit mode. Vector-tile road lengths are labelled as engineering approximations.
- The advanced workflow combines main roads, subway stations, universities, water avoidance
  and boundary clearance. Hard constraints remove invalid cells before weighted ranking.
- Conversation memory now carries the previous task, POI category and distance as well as the
  current region. `把范围改为2公里` therefore produces a new executable workflow using the
  earlier task context.

The tool is intentionally a screening aid. OSM data and engineering weights do not replace
planning, cadastral, environmental or field investigation.

## Run

```powershell
python scripts\build_knowledge.py
python scripts\run_api.py
```

`run_api.py` 默认同时启动 FastAPI 和一个独立的常驻 Worker 子进程。只有在 Docker 或已有
进程管理器负责 Worker 时，才使用 `python scripts\run_api.py --api-only`，并在另一个终端
执行 `python scripts\run_worker.py`。不要给网页演示使用 `--once`，该参数只处理一个任务
就退出。Worker 心跳写入 `outputs/worker_heartbeat.json`；无心跳的排队任务会在宽限期后
明确失败为 `WORKER_UNAVAILABLE`，不再无限停留在 `PENDING`。
页面会显示排队序号和 Worker 当前任务短 ID；启动时超过
`WORKER_PENDING_MAX_AGE_SECONDS` 的旧排队任务会标记为 `QUEUE_EXPIRED`，避免一次重启先
执行很久以前的演示请求。执行节点进一步拆成逐 GIS 工具的 SSE 事件，能够直接看到当前
是道路下载、缓冲区、投影、裁剪还是统计步骤。

Open `http://127.0.0.1:8000/`. Recommended multi-turn acceptance sequence:

```text
分析南京市医院1公里服务覆盖范围
把范围改为2公里
再分析这里的医院空间密度
```

The frontend requires network access to the Leaflet CDN and OpenStreetMap tiles. The backend
result download and API remain available when the basemap is unavailable.

## Verification

Run:

```powershell
python scripts\evaluate.py
```

The offline suite covers all new workflow schemas, deterministic planner routes, true follow-up
context inheritance, density/distance tools, advanced site selection, frontend route, SSE route,
GeoJSON route and progress logs while retaining all State 5 tests.
