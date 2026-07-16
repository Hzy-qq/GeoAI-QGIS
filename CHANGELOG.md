# Changelog

## 1.0.1-final-data-reliability

- Added a bundled Jiangsu OSM PBF snapshot and normalized Nanjing boundary, 11-POI, main-road and water GeoPackages.
- Changed acquisition to persistent cache -> normalized offline pack -> local PBF -> allowlisted network fallback.
- Added atomic JSON/layer caches, resumable POI tiles, endpoint circuit breakers, exponential backoff and partial-coverage provenance.
- Added `scripts/build_osm_offline_pack.py` and snapshot/source fields in result layers and summaries.
- Expanded the offline suite to 75 tests and replayed subway-density and main-road workflows end to end without Overpass.

## 1.0.0-final

- Added the polished Leaflet multi-turn frontend, SSE tool progress and result-layer downloads.
- Added Redis event mirroring, Worker leases and result caching with MySQL/file fallbacks.
- Added POI nearest-neighbor, service-gap and multi-ring coverage workflows.
- Bounded online main-road acquisition and exposed readiness/queue failure states.
- Expanded the offline suite to 67 tests and added BGE retrieval evaluation.

## 0.7.0-state5

- Added conversation/thread APIs and MySQL conversation tables.
- Added structured memory, bounded history compaction and reference clarification.
- Added an outer LangGraph with durable SQLite checkpoints.
- Added a deterministic adjacent-region workflow and Nanjing topology fixture.
- Added multi-turn, API and topology acceptance tests.

## 0.6.0-state4

- Added asynchronous FastAPI, Worker, MySQL task storage and artifact APIs.
