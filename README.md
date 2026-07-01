# GeoAI-QGIS State 3

Stage 3 is a LangGraph GIS agent that can determine missing datasets, download
allowlisted OpenStreetMap data at runtime, validate and reproject it, execute QGIS
tools, evaluate the result, and return a Chinese LLM summary.

## Supported tasks

1. `统计南京市所有大学周边500米范围内的道路总长度`
2. `南京市面积是多少`
3. `南京市有多少个高校要素`

The first task dissolves all university buffers before road statistics, so roads
inside overlapping buffers are counted once. "All universities" means the current
OSM `amenity=university|college` features, not an official institution list.

## Runtime flow

```text
query
  -> Chroma dense retrieval -> Cross-Encoder rerank
  -> DeepSeek native submit_gis_plan tool call
  -> workflow/schema/data-source validation
  -> download boundary and POIs
  -> auto UTM projection -> dissolved buffer
  -> download roads -> match CRS -> QGIS clip/sum
  -> deterministic evaluator
  -> LLM summary
```

The LLM never supplies a URL. Dataset IDs are resolved by `dataset_catalog.py` to
Nominatim or Overpass. Every output path must use `workspace://`, which is resolved
under `outputs/tasks/<task_id>`.

DeepSeek V4 enables thinking mode by default. This project sets
`LLM_TOOL_CALL_THINKING=disabled` for the forced `submit_gis_plan` call because the
provider does not allow forced `tool_choice` in thinking mode.

## Installation

Run commands from this directory:

```powershell
cd F:\研一\GitHub项目\qgis-geoagent\QGIS_AI_State_3
F:\anaconda3\envs\pytorch\python.exe -m pip install -r requirements-advanced.txt
Copy-Item .env.example .env
```

Fill in `LLM_API_KEY` in `.env`. Do not commit `.env`.

## Execution order

```powershell
F:\anaconda3\envs\pytorch\python.exe scripts/check_runtime.py
F:\anaconda3\envs\pytorch\python.exe scripts/run_tests.py
F:\anaconda3\envs\pytorch\python.exe scripts/build_chroma_store.py
F:\anaconda3\envs\pytorch\python.exe scripts/retrieve_chroma_knowledge.py "大学周边道路长度"
F:\anaconda3\envs\pytorch\python.exe scripts/test_dynamic_data.py 南京市
F:\anaconda3\envs\pytorch\python.exe scripts/smoke_test_dynamic_workflow.py --region 南京市 --distance 500
F:\anaconda3\envs\pytorch\python.exe scripts/run_state3_agent.py
```

No business data preparation script is required. The first real task downloads data
and model files, so it is slower. Later runs can use the data and model caches.

For Jiangsu cities, `ROAD_SOURCE_MODE=auto` prefers the daily Geofabrik Jiangsu PBF
extract and filters roads locally. This avoids using public Overpass for a city-scale
road network. Other regions fall back to bounded Overpass grid queries. Set
`ROAD_SOURCE_MODE=geofabrik` to forbid fallback or `overpass` to test only Overpass.

## Evaluation

Offline unit tests:

```powershell
python scripts/evaluate_stage3.py
```

Retrieval evaluation after building Chroma:

```powershell
python scripts/evaluate_stage3.py --retrieval
```

Live planner and end-to-end evaluations consume API/network resources:

```powershell
python scripts/evaluate_stage3.py --planner-live
python scripts/evaluate_stage3.py --e2e-live
```

## Safety and limits

- Source hosts are allowlisted; model-generated URLs are rejected.
- Query area, response bytes, feature count, HTTP timeout and QGIS timeout are bounded.
- Planning is attempted at most twice; transient execution is retried once.
- Result files and required statistic fields must pass the evaluator before summary.
- Raw data, caches, task outputs and `.env` are excluded by `.gitignore`.
