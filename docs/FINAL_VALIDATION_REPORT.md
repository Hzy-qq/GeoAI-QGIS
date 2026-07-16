# Final validation report

Date: 2026-07-15 (Asia/Shanghai)

## Verified in this workspace

| Check | Command | Result |
|---|---|---|
| Offline regression | `F:\anaconda3\envs\pytorch\python.exe scripts\evaluate.py` | **75/75 passed** (about 7 s on this machine) |
| Local BGE retrieval | `python scripts\evaluate.py --retrieval` | 6 cases; Recall@4 **0.917**, MRR **1.000** |
| Compose parsing | `docker-compose --env-file .env.example config --quiet` | Passed |
| Redis absent fallback | included in offline suite | Passed |
| New spatial workflows | included in offline suite | Geometry outputs and routing passed |
| Live infrastructure | isolated MySQL 8.4 + Redis 7.4 containers | Both `healthy`; `mysqladmin` alive; Redis `PONG` |
| Code-to-infrastructure | SQLAlchemy + Redis client probe | MySQL schema/`SELECT 1`, JSON cache and Redis Stream writes passed |
| Offline data pack replay | cache disabled; boundary/POI/roads/water handlers | Nanjing boundary **0.952 s**, subway **0.089 s**, main roads **0.293 s**, water **0.206 s**; zero network calls |
| Subway density end-to-end | `run_langgraph_agent("分析南京市地铁站空间密度")` | Success in **44.67 s**; 260 station features, 308 grids; Evaluator passed |
| Main-road end-to-end | `run_langgraph_agent("统计南京市高校周边1公里主要道路总长度")` | Success in **49.59 s**; 923.84 km, 2,403 road features; Evaluator passed |

## Data reliability boundary

The validation workspace used a local `jiangsu-latest.osm.pbf` snapshot and 14 generated Nanjing GeoPackages for the boundary, 11 POI types, main roads and water. These runtime datasets are deliberately excluded from Git; only acquisition, validation and pack-building code is published. After local preparation, Nanjing demonstrations do not require Nominatim, Overpass or vector-tile availability. Every local result retains snapshot provenance and is explicitly described as non-real-time, non-official OSM data.

When an optional Jiangsu PBF is configured, other Jiangsu extents use it and write a persistent normalized cache. Without local data, the fallback network path uses bounded retries, endpoint circuit breakers, atomic caches and resumable POI tiles; it can still fail visibly when every approved upstream source is unavailable.

## Environment-dependent release checks

The infrastructure layer was exercised with isolated ports 13306/16379 and cleaned up without deleting volumes. Before a demonstration run:

1. Optionally prepare local OSM data using `data/osm/README.md` for network-independent demos.
2. Start Docker Desktop and run `docker compose up -d mysql redis` (or `docker-compose`).
3. Build Chroma with `python scripts\build_knowledge.py`.
4. Run `python scripts\run_api.py` and wait for the companion Worker log.
5. Run `python scripts\evaluate.py --check-runtime`, then submit the two documented smoke cases.

External OSM refresh and LLM availability are deliberately excluded from the deterministic offline suite.

The road replay emitted a Pyogrio warning that QGIS wrote GeoPackage 1.4 while the
current reader may only partially support that version. The result layer remained
readable and passed the required-field/value Evaluator; treat the warning as a
runtime-version compatibility item, not as a failed validation.
