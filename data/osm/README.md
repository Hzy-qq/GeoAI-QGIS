# OSM runtime data (not stored in Git)

The public repository intentionally contains no PBF, GeoPackage, database, cache or task-output
data. The complete acquisition, validation and offline-pack build code remains versioned.

For the reproducible Nanjing demo, download the optional Jiangsu Geofabrik extract locally:

```powershell
New-Item -ItemType Directory -Force data\osm | Out-Null
Invoke-WebRequest https://download.geofabrik.de/asia/china/jiangsu-latest.osm.pbf `
  -OutFile data\osm\jiangsu-latest.osm.pbf
python scripts\build_osm_offline_pack.py
```

This creates `data/osm/nanjing/boundary.gpkg`, 11 `poi_*.gpkg` layers,
`main_roads.gpkg` and `water.gpkg`. All generated files are ignored by Git. You may instead set
`OSM_LOCAL_PBF_PATH` to an existing local extract.

Without local data, the `auto` source mode uses the allowlisted network sources with bounded
retries, circuit breakers, atomic caches and visible failure semantics. OSM/Geofabrik data is
community-maintained ODbL data, not an official or real-time GIS inventory.
