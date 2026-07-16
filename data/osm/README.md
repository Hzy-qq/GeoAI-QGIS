# OSM offline runtime pack

This folder makes the default Nanjing demonstrations independent of public OSM
endpoints.

- `jiangsu-latest.osm.pbf` is the local Jiangsu OSM/Geofabrik snapshot
  (`SHA256 66B841FEA4A9FAC62C7FBD4F3DB7765DA04630F1ACED4F1CEA2453EA9D1243D7`).
- `nanjing/boundary.gpkg` is the normalized Nanjing administrative boundary.
- `nanjing/poi_*.gpkg` contains every supported POI type.
- `nanjing/main_roads.gpkg` contains motorway, trunk, primary, secondary and links.
- `nanjing/water.gpkg` contains OSM water polygons used by site-selection tools.

The layers are ODbL data derived from OpenStreetMap contributors. They are
community-maintained snapshots, not current official GIS inventories.

To replace the snapshot, update `jiangsu-latest.osm.pbf` (or set
`OSM_LOCAL_PBF_PATH`) and run from the repository root:

```powershell
python scripts\build_osm_offline_pack.py
```

Use the default `auto` source modes. Explicit `overpass`/`vector_tiles` modes are
mainly for refresh smoke tests and require a working network.
